from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from dcc_mcp_wwise import _waapi_probe_worker, waapi

_HELPER = Path(__file__).with_name("waapi_probe_helper.py")
_FAKE_CLIENT = Path(__file__).with_name("fake_waapi_client")


def _pid_exists(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(0x00100000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for_pid(path: Path) -> int:
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if path.is_file():
            return int(path.read_text(encoding="ascii"))
        time.sleep(0.01)
    raise AssertionError("probe helper did not publish its PID")


def _assert_pid_dead(pid: int) -> None:
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if not _pid_exists(pid):
            return
        time.sleep(0.01)
    assert not _pid_exists(pid)


def _helper_command(
    pid_path: Path,
    descendant_path: Path,
    ready_path: Path,
    status_path: Path | None = None,
) -> list[str]:
    command = [
        sys.executable,
        str(_HELPER),
        str(pid_path),
        str(descendant_path),
        str(ready_path),
    ]
    if status_path is not None:
        command.append(str(status_path))
    return command


def test_get_info_timeout_reaps_the_owned_probe_tree(monkeypatch, tmp_path):
    pid_path = tmp_path / "probe.pid"
    descendant_path = tmp_path / "descendant.pid"
    ready_path = tmp_path / "probe.ready"
    monkeypatch.setattr(
        waapi,
        "_probe_worker_command",
        lambda _status_path, _url: _helper_command(pid_path, descendant_path, ready_path),
    )

    started = time.monotonic()
    with pytest.raises(waapi.WaapiTimeoutError) as raised:
        waapi.get_wwise_info(timeout_secs=2.0)
    elapsed = time.monotonic() - started

    pid = _wait_for_pid(pid_path)
    descendant = _wait_for_pid(descendant_path)
    assert elapsed < 3.5
    assert raised.value.failure_stage == "deadline"
    assert raised.value.failure_type == "timeout"
    _assert_pid_dead(pid)
    _assert_pid_dead(descendant)


def test_get_info_cancellation_reaps_the_owned_probe_tree(monkeypatch, tmp_path):
    pid_path = tmp_path / "probe.pid"
    descendant_path = tmp_path / "descendant.pid"
    ready_path = tmp_path / "probe.ready"

    class Cancelled(RuntimeError):
        pass

    monkeypatch.setattr(
        waapi,
        "_probe_worker_command",
        lambda _status_path, _url: _helper_command(pid_path, descendant_path, ready_path),
    )

    def cancel_when_ready() -> None:
        if ready_path.is_file():
            raise Cancelled("cancelled")

    monkeypatch.setattr(waapi, "check_dcc_cancelled", cancel_when_ready)

    with pytest.raises(Cancelled):
        waapi.get_wwise_info(timeout_secs=5)

    pid = _wait_for_pid(pid_path)
    descendant = _wait_for_pid(descendant_path)
    _assert_pid_dead(pid)
    _assert_pid_dead(descendant)


def test_get_info_success_reaps_the_owned_probe_tree(monkeypatch, tmp_path):
    pid_path = tmp_path / "probe.pid"
    descendant_path = tmp_path / "descendant.pid"
    ready_path = tmp_path / "probe.ready"
    monkeypatch.setattr(
        waapi,
        "_probe_worker_command",
        lambda status_path, _url: _helper_command(
            pid_path, descendant_path, ready_path, status_path
        ),
    )

    result = waapi.get_wwise_info(timeout_secs=2)

    pid = _wait_for_pid(pid_path)
    descendant = _wait_for_pid(descendant_path)
    assert result == {"version": {"displayName": "2024.1.0.0"}}
    _assert_pid_dead(pid)
    _assert_pid_dead(descendant)


def test_worker_preserves_rpc_failure_over_disconnect_and_redacts_details(tmp_path):
    status_path = tmp_path / "status.json"

    class FailingClient:
        def __init__(self, _url, allow_exception):
            assert allow_exception is True

        def call(self, _uri, _arguments, options):
            assert options == {}
            raise RuntimeError("token=secret C:/private/project.wproj")

        def disconnect(self):
            raise RuntimeError("certificate=C:/private/client.pem")

    code = _waapi_probe_worker.execute_probe(
        waapi.DEFAULT_WAAPI_URL,
        status_path,
        FailingClient,
    )

    raw = status_path.read_text(encoding="utf-8")
    assert code == 40
    assert json.loads(raw) == {
        "ok": False,
        "failure_stage": "rpc",
        "failure_type": "rpc_failed",
    }
    assert "secret" not in raw
    assert "private" not in raw


def test_durable_probe_failure_wins_a_concurrent_cancellation(monkeypatch):
    class Cancelled(RuntimeError):
        pass

    class RunningProcess:
        @staticmethod
        def poll():
            return None

    records = iter([None, {"ok": False, "failure_stage": "rpc", "failure_type": "rpc_failed"}])
    monkeypatch.setattr(waapi, "_start_probe", lambda _command: (RunningProcess(), object()))
    monkeypatch.setattr(waapi, "_read_probe_status", lambda _path: next(records))
    monkeypatch.setattr(waapi, "_cleanup_probe", lambda _process, _owner: None)
    monkeypatch.setattr(
        waapi,
        "check_dcc_cancelled",
        lambda: (_ for _ in ()).throw(Cancelled("cancelled")),
    )

    with pytest.raises(waapi.WaapiCallError) as raised:
        waapi.get_wwise_info(timeout_secs=2)

    assert raised.value.failure_stage == "rpc"
    assert raised.value.failure_type == "rpc_failed"


@pytest.mark.parametrize("phase", ["construction", "rpc", "disconnect"])
def test_one_absolute_deadline_covers_every_official_client_phase(monkeypatch, tmp_path, phase):
    ready_path = tmp_path / "phase.ready"
    inherited = os.environ.get("PYTHONPATH")
    pythonpath = str(_FAKE_CLIENT)
    if inherited:
        pythonpath += os.pathsep + inherited
    monkeypatch.setenv("PYTHONPATH", pythonpath)
    monkeypatch.setenv("DCC_MCP_WWISE_TEST_PHASE", phase)
    monkeypatch.setenv("DCC_MCP_WWISE_TEST_READY", str(ready_path))

    started = time.monotonic()
    with pytest.raises(waapi.WaapiTimeoutError):
        waapi.get_wwise_info(timeout_secs=5.0)

    assert time.monotonic() - started < 6.5
    assert ready_path.read_text(encoding="ascii") == phase


def test_isolated_official_client_completes_all_phases(monkeypatch):
    inherited = os.environ.get("PYTHONPATH")
    pythonpath = str(_FAKE_CLIENT)
    if inherited:
        pythonpath += os.pathsep + inherited
    monkeypatch.setenv("PYTHONPATH", pythonpath)
    monkeypatch.delenv("DCC_MCP_WWISE_TEST_PHASE", raising=False)
    monkeypatch.delenv("DCC_MCP_WWISE_TEST_READY", raising=False)

    assert waapi.get_wwise_info(timeout_secs=5) == {"version": {"displayName": "2024.1.0.0"}}
