"""Small, bounded wrapper around Audiokinetic's official WAAPI client."""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping
from urllib.parse import urlparse

from dcc_mcp_core import check_dcc_cancelled

DEFAULT_WAAPI_URL = "ws://127.0.0.1:8080/waapi"
_DEFAULT_PROBE_TIMEOUT_SECS = 5.0
_PROBE_CLEANUP_SECS = 1.0
_PROBE_POLL_SECS = 0.02
_MAX_PROBE_STATUS_BYTES = 64 * 1024
_LOGGER = logging.getLogger(__name__)


class WaapiConnectionError(RuntimeError):
    """The official client could not establish a WAAPI session."""

    def __init__(
        self,
        message: str,
        *,
        failure_stage: str = "connect",
        failure_type: str = "connection_failed",
    ) -> None:
        super().__init__(message)
        self.failure_stage = failure_stage
        self.failure_type = failure_type


class WaapiCallError(RuntimeError):
    """An established WAAPI session rejected or malformed a typed call."""

    def __init__(
        self,
        message: str,
        *,
        failure_stage: str = "rpc",
        failure_type: str = "rpc_failed",
    ) -> None:
        super().__init__(message)
        self.failure_stage = failure_stage
        self.failure_type = failure_type


class WaapiTimeoutError(WaapiCallError):
    """The isolated official getInfo probe exceeded its absolute deadline."""

    def __init__(self) -> None:
        super().__init__(
            "The WAAPI probe exceeded its deadline",
            failure_stage="deadline",
            failure_type="timeout",
        )


class WaapiEndpointPolicyError(ValueError):
    """A remote WAAPI endpoint is outside the operator-approved policy."""


def resolve_waapi_url(value: str | None = None) -> str:
    configured = (
        value if value is not None else os.environ.get("DCC_MCP_WWISE_WAAPI_URL", DEFAULT_WAAPI_URL)
    )
    url = configured.strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"ws", "wss"} or not parsed.hostname or not parsed.port:
        raise ValueError("WAAPI URL must be an absolute ws:// or wss:// URL with a port")
    if parsed.path != "/waapi" or parsed.params or parsed.query or parsed.fragment:
        raise ValueError("WAAPI URL must use the official /waapi path without parameters")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("WAAPI URL must not contain credentials")
    host = parsed.hostname.strip().lower()
    loopback = is_loopback_waapi_url(url)
    if not loopback:
        if parsed.scheme != "wss":
            raise WaapiEndpointPolicyError("Remote WAAPI endpoints must use wss://")
        allowed = {
            item.strip().lower()
            for item in os.environ.get("DCC_MCP_WWISE_WAAPI_ALLOWED_HOSTS", "").split(",")
            if item.strip()
        }
        if host not in allowed:
            raise WaapiEndpointPolicyError(
                "Remote WAAPI host is not in DCC_MCP_WWISE_WAAPI_ALLOWED_HOSTS allowlist"
            )
    return url


def _client_type():
    from waapi import WaapiClient

    return WaapiClient


def _probe_worker_command(status_path: Path, url: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "dcc_mcp_wwise._waapi_probe_worker",
        "--supervise",
        str(status_path),
        url,
    ]


def _read_probe_status(path: Path) -> dict[str, Any] | None:
    try:
        size = path.stat().st_size
        if size <= 0 or size > _MAX_PROBE_STATUS_BYTES:
            raise WaapiCallError(
                "The WAAPI probe returned an invalid result",
                failure_stage="protocol",
                failure_type="invalid_result",
            )
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WaapiCallError(
            "The WAAPI probe returned an invalid result",
            failure_stage="protocol",
            failure_type="invalid_result",
        ) from exc
    if not isinstance(value, dict) or not isinstance(value.get("ok"), bool):
        raise WaapiCallError(
            "The WAAPI probe returned an invalid result",
            failure_stage="protocol",
            failure_type="invalid_result",
        )
    return value


class _ProbeOwner(AbstractContextManager["_ProbeOwner"]):
    """Own the complete private probe tree until bounded cleanup finishes."""

    def terminate(self) -> None:
        raise NotImplementedError

    def __exit__(self, *_exc: object) -> None:
        return None


class _PosixProbeOwner(_ProbeOwner):
    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self._process = process

    def terminate(self) -> None:
        # The private supervisor remains the group leader until this call. A direct
        # SIGKILL cannot leave a leader-exit window where its numeric PGID is reused.
        os.killpg(self._process.pid, signal.SIGKILL)


class _WindowsProbeOwner(_ProbeOwner):
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _THREAD_SUSPEND_RESUME = 0x0002
    _TH32CS_SNAPTHREAD = 0x00000004

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        class _IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class _BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BasicLimitInformation),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        self._ctypes = ctypes
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        self._kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        self._kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        self._kernel32.SetInformationJobObject.restype = wintypes.BOOL
        self._kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self._kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        self._kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self._kernel32.TerminateJobObject.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL

        handle = self._kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        self._handle = handle
        limits = _ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = self._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self._kernel32.SetInformationJobObject(
            handle,
            self._JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            error = ctypes.get_last_error()
            self._kernel32.CloseHandle(handle)
            self._handle = None
            raise OSError(error, "SetInformationJobObject failed")

    def assign(self, process: subprocess.Popen[bytes]) -> None:
        if not self._kernel32.AssignProcessToJobObject(self._handle, int(process._handle)):
            raise OSError(self._ctypes.get_last_error(), "AssignProcessToJobObject failed")

    def terminate(self) -> None:
        if self._handle and not self._kernel32.TerminateJobObject(self._handle, 1):
            raise OSError(self._ctypes.get_last_error(), "TerminateJobObject failed")

    def __exit__(self, *_exc: object) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


def _resume_windows_process(process: subprocess.Popen[bytes]) -> None:
    import ctypes
    from ctypes import wintypes

    class _ThreadEntry32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", wintypes.LONG),
            ("tpDeltaPri", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry32)]
    kernel32.Thread32First.restype = wintypes.BOOL
    kernel32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry32)]
    kernel32.Thread32Next.restype = wintypes.BOOL
    kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenThread.restype = wintypes.HANDLE
    kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel32.ResumeThread.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    snapshot = kernel32.CreateToolhelp32Snapshot(_WindowsProbeOwner._TH32CS_SNAPTHREAD, 0)
    if snapshot == wintypes.HANDLE(-1).value:
        raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot failed")
    resumed = False
    try:
        entry = _ThreadEntry32()
        entry.dwSize = ctypes.sizeof(entry)
        present = kernel32.Thread32First(snapshot, ctypes.byref(entry))
        while present:
            if entry.th32OwnerProcessID == process.pid:
                thread = kernel32.OpenThread(
                    _WindowsProbeOwner._THREAD_SUSPEND_RESUME,
                    False,
                    entry.th32ThreadID,
                )
                if thread:
                    try:
                        if kernel32.ResumeThread(thread) != 0xFFFFFFFF:
                            resumed = True
                    finally:
                        kernel32.CloseHandle(thread)
            present = kernel32.Thread32Next(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    if not resumed:
        raise OSError("No suspended probe thread could be resumed")


def _start_probe(command: list[str]) -> tuple[subprocess.Popen[bytes], _ProbeOwner]:
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "posix":
        process = subprocess.Popen(command, start_new_session=True, **kwargs)
        return process, _PosixProbeOwner(process)
    if os.name == "nt":
        owner = _WindowsProbeOwner()
        process: subprocess.Popen[bytes] | None = None
        try:
            process = subprocess.Popen(
                command,
                creationflags=subprocess.CREATE_NO_WINDOW | 0x00000004,
                **kwargs,
            )
            owner.assign(process)
            _resume_windows_process(process)
            return process, owner
        except BaseException:
            try:
                owner.terminate()
            except OSError:
                if process is not None and process.poll() is None:
                    process.kill()
            if process is not None:
                try:
                    process.wait(timeout=_PROBE_CLEANUP_SECS)
                except subprocess.TimeoutExpired:
                    pass
            owner.__exit__(None, None, None)
            raise
    process = subprocess.Popen(command, **kwargs)
    return process, _ProbeOwner()


def _cleanup_probe(process: subprocess.Popen[bytes], owner: _ProbeOwner) -> None:
    primary: BaseException | None = None
    try:
        owner.terminate()
    except BaseException as exc:
        primary = exc
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
    try:
        process.wait(timeout=_PROBE_CLEANUP_SECS)
    except (OSError, subprocess.TimeoutExpired) as exc:
        primary = primary or exc
    finally:
        owner.__exit__(None, None, None)
    if primary is not None or process.poll() is None:
        raise WaapiCallError(
            "The WAAPI probe cleanup failed",
            failure_stage="cleanup",
            failure_type="cleanup_failed",
        ) from primary


def _probe_failure(record: dict[str, Any]) -> WaapiCallError:
    stage = record.get("failure_stage")
    failure_type = record.get("failure_type")
    allowed = {
        ("connect", "connection_failed"),
        ("rpc", "rpc_failed"),
        ("disconnect", "cleanup_failed"),
        ("protocol", "invalid_result"),
    }
    if (
        not isinstance(stage, str)
        or not isinstance(failure_type, str)
        or (stage, failure_type) not in allowed
    ):
        return WaapiCallError(
            "The WAAPI probe returned an invalid result",
            failure_stage="protocol",
            failure_type="invalid_result",
        )
    if stage == "connect":
        return WaapiConnectionError(
            "The WAAPI connection could not be established",
            failure_stage=stage,
            failure_type=failure_type,
        )
    messages = {
        "rpc": "The WAAPI getInfo RPC failed",
        "disconnect": "The WAAPI probe disconnect failed",
        "protocol": "The WAAPI probe returned an invalid result",
    }
    return WaapiCallError(
        messages.get(stage, "The WAAPI probe failed"),
        failure_stage=stage,
        failure_type=failure_type,
    )


def _isolated_get_wwise_info(url: str, timeout_secs: float) -> dict[str, Any]:
    if not isinstance(timeout_secs, (int, float)) or isinstance(timeout_secs, bool):
        raise ValueError("WAAPI timeout must be a number")
    timeout = float(timeout_secs)
    if timeout <= 0 or timeout > 120:
        raise ValueError("WAAPI timeout must be greater than 0 and no more than 120 seconds")
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="dcc-mcp-wwise-probe-") as temporary:
        status_path = Path(temporary) / "status.json"
        process, owner = _start_probe(_probe_worker_command(status_path, url))
        deadline = started + timeout
        record: dict[str, Any] | None = None
        primary: BaseException | None = None
        while True:
            try:
                record = _read_probe_status(status_path)
            except BaseException as exc:
                primary = exc
                break
            if record is not None:
                if record["ok"] is False:
                    primary = _probe_failure(record)
                break
            if process.poll() is not None:
                primary = WaapiCallError(
                    "The WAAPI probe returned no result",
                    failure_stage="protocol",
                    failure_type="missing_result",
                )
                break
            try:
                check_dcc_cancelled()
            except BaseException as exc:
                try:
                    terminal = _read_probe_status(status_path)
                except BaseException as protocol_error:
                    primary = protocol_error
                else:
                    if terminal is None:
                        primary = exc
                    else:
                        record = terminal
                        primary = _probe_failure(terminal) if terminal["ok"] is False else None
                break
            if time.monotonic() >= deadline:
                try:
                    terminal = _read_probe_status(status_path)
                except BaseException as protocol_error:
                    primary = protocol_error
                else:
                    if terminal is None:
                        primary = WaapiTimeoutError()
                    else:
                        record = terminal
                        primary = _probe_failure(terminal) if terminal["ok"] is False else None
                break
            time.sleep(min(_PROBE_POLL_SECS, max(0.0, deadline - time.monotonic())))
        try:
            _cleanup_probe(process, owner)
        except BaseException as cleanup_error:
            if primary is not None:
                raise primary from cleanup_error
            raise
        if primary is not None:
            raise primary
        assert record is not None
        result = record.get("result")
        if not isinstance(result, dict):
            raise WaapiCallError(
                "The WAAPI probe returned an invalid result",
                failure_stage="protocol",
                failure_type="invalid_result",
            )
        return result


def is_loopback_waapi_url(url: str) -> bool:
    """Return whether a validated WAAPI URL targets the local host."""
    host = urlparse(url).hostname
    if host is None:
        return False
    normalized = host.strip().lower()
    try:
        return normalized == "localhost" or ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return normalized == "localhost"


@contextmanager
def connect_waapi(url: str | None = None) -> Iterator[Any]:
    resolved = resolve_waapi_url(url)
    try:
        client = _client_type()(resolved, allow_exception=True)
    except Exception as exc:
        _LOGGER.debug("WAAPI connection failed (%s)", type(exc).__name__)
        raise WaapiConnectionError("The WAAPI connection could not be established") from exc
    try:
        yield client
    except BaseException:
        try:
            client.disconnect()
        except Exception as exc:
            _LOGGER.debug("WAAPI cleanup failed after primary error (%s)", type(exc).__name__)
        raise
    else:
        try:
            client.disconnect()
        except Exception as exc:
            _LOGGER.debug("WAAPI disconnect failed (%s)", type(exc).__name__)
            raise WaapiCallError(
                "The WAAPI probe disconnect failed",
                failure_stage="disconnect",
                failure_type="cleanup_failed",
            ) from exc


def call_waapi(
    uri: str,
    arguments: Mapping[str, Any] | None = None,
    *,
    options: Mapping[str, Any] | None = None,
    url: str | None = None,
    result_validator: Callable[[Any], Any] | None = None,
) -> Any:
    with connect_waapi(url) as client:
        try:
            result = client.call(uri, dict(arguments or {}), options=dict(options or {}))
        except Exception as exc:
            _LOGGER.debug("WAAPI RPC failed (%s)", type(exc).__name__)
            raise WaapiCallError("The WAAPI RPC failed") from exc
        if result is None:
            raise WaapiCallError("The WAAPI RPC returned no result")
        if result_validator is not None:
            result = result_validator(result)
    return result


def _require_info_result(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise WaapiCallError(
            "The WAAPI probe returned an invalid result",
            failure_stage="protocol",
            failure_type="invalid_result",
        )
    return result


def get_wwise_info(
    url: str | None = None,
    *,
    timeout_secs: float = _DEFAULT_PROBE_TIMEOUT_SECS,
) -> dict[str, Any]:
    resolved = resolve_waapi_url(url)
    return _require_info_result(_isolated_get_wwise_info(resolved, timeout_secs))


def get_wwise_version(url: str | None = None) -> str:
    version = get_wwise_info(url).get("version", "unknown")
    if isinstance(version, dict):
        return str(version.get("displayName") or version.get("name") or "unknown")
    return str(version)


def is_connected(
    url: str | None = None,
    *,
    timeout_secs: float = _DEFAULT_PROBE_TIMEOUT_SECS,
) -> bool:
    try:
        get_wwise_info(url, timeout_secs=timeout_secs)
    except (RuntimeError, ValueError):
        return False
    return True
