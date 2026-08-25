"""Independent OS observation of one exact Wwise Authoring process."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

_WWISE_EXECUTABLE_NAMES = {"wwise", "wwise.exe"}
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_OBSERVATION_TIMEOUT_SECS = 3.0


class ProcessIdentityError(RuntimeError):
    """A Wwise process identity could not be proved without ambiguity."""

    def __init__(self, failure_type: str) -> None:
        super().__init__(failure_type)
        self.failure_type = failure_type


@dataclass(frozen=True)
class WwiseProcessIdentity:
    pid: int
    executable: str
    started_at: str

    def as_public_check(self, *, success: bool) -> dict[str, object]:
        return {
            "success": success,
            "pid": self.pid,
            "executable": self.executable,
            "started_at": self.started_at,
        }


def _require_wwise(identity: WwiseProcessIdentity) -> WwiseProcessIdentity:
    if identity.executable.casefold() not in _WWISE_EXECUTABLE_NAMES:
        raise ProcessIdentityError("identity_mismatch")
    return identity


def _observe_windows(pid: int) -> WwiseProcessIdentity:
    import ctypes
    from ctypes import wintypes

    class FileTime(ctypes.Structure):
        _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        raise ProcessIdentityError("identity_unavailable")
    try:
        size = wintypes.DWORD(32768)
        image = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)):
            raise ProcessIdentityError("identity_unavailable")
        created = FileTime()
        exited = FileTime()
        kernel = FileTime()
        user = FileTime()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            raise ProcessIdentityError("identity_unavailable")
        started_at = str((int(created.high) << 32) | int(created.low))
        return WwiseProcessIdentity(pid, Path(image.value).name, started_at)
    finally:
        kernel32.CloseHandle(handle)


def _observe_linux(pid: int) -> WwiseProcessIdentity:
    proc = Path("/proc") / str(pid)
    try:
        executable = (proc / "exe").resolve(strict=True).name
        stat = (proc / "stat").read_text(encoding="ascii")
        fields = stat[stat.rfind(")") + 2 :].split()
        started_at = fields[19]
    except (IndexError, OSError, UnicodeError):
        raise ProcessIdentityError("identity_unavailable") from None
    return WwiseProcessIdentity(pid, executable, started_at)


def _observe_ps(pid: int) -> WwiseProcessIdentity:
    try:
        completed = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm=", "-o", "lstart="],
            check=False,
            capture_output=True,
            text=True,
            timeout=_OBSERVATION_TIMEOUT_SECS,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ProcessIdentityError("identity_unavailable") from None
    parts = completed.stdout.strip().split()
    if completed.returncode != 0 or len(parts) < 6:
        raise ProcessIdentityError("identity_unavailable")
    executable = Path(" ".join(parts[:-5])).name
    started_at = " ".join(parts[-5:])
    return WwiseProcessIdentity(pid, executable, started_at)


def observe_wwise_process(pid: int) -> WwiseProcessIdentity:
    """Return PID/executable/start identity, rejecting non-Wwise processes."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise ProcessIdentityError("identity_unavailable")
    if os.name == "nt":
        identity = _observe_windows(pid)
    elif os.name == "posix" and Path("/proc").is_dir():
        identity = _observe_linux(pid)
    else:
        identity = _observe_ps(pid)
    return _require_wwise(identity)


def require_same_identity(
    expected: WwiseProcessIdentity,
    observed: WwiseProcessIdentity,
) -> None:
    if observed != expected:
        raise ProcessIdentityError("identity_mismatch")


__all__ = [
    "ProcessIdentityError",
    "WwiseProcessIdentity",
    "observe_wwise_process",
    "require_same_identity",
]
