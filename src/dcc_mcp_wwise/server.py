"""Out-of-process DCC-MCP server bound to one Wwise Authoring instance."""

from __future__ import annotations

import argparse
import csv
import io
import logging
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Optional, Sequence

from dcc_mcp_core import DccServerOptions, HostExecutionBridge
from dcc_mcp_core.readiness import AdapterReadinessBinder
from dcc_mcp_core.server_base import DccServerBase

from . import process_identity, waapi
from .__version__ import __version__
from .dispatcher import WwiseWaapiDispatcher
from .menu import WwiseMenu

_server: Optional["WwiseMcpServer"] = None
_LOGGER = logging.getLogger(__name__)


def _detect_wwise_pids() -> list[int]:
    if os.name == "nt":
        try:
            completed = subprocess.run(
                ["tasklist", "/fo", "csv", "/nh", "/fi", "imagename eq Wwise.exe"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        if completed.returncode != 0:
            return []
        rows = csv.reader(io.StringIO(completed.stdout))
        return [int(row[1]) for row in rows if len(row) > 1 and row[0].lower() == "wwise.exe"]

    try:
        completed = subprocess.run(
            ["pgrep", "-x", "Wwise"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [int(value) for value in completed.stdout.split() if value.isdigit()]


def _resolve_host_pid(host_pid: int | None) -> int:
    resolved = host_pid or int(os.environ.get("DCC_MCP_WWISE_HOST_PID", "0"))
    if resolved > 0:
        return resolved
    candidates = _detect_wwise_pids()
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ValueError("No Wwise Authoring process was found")
    raise ValueError("Multiple Wwise processes were found; pass --host-pid explicitly")


class WwiseMcpServer(DccServerBase):
    """DCC-MCP service backed by Wwise's official loopback WAAPI endpoint."""

    def __init__(
        self,
        port: int | None = None,
        host_pid: int | None = None,
        waapi_url: str | None = None,
    ) -> None:
        resolved_pid = _resolve_host_pid(host_pid)
        identity_before = process_identity.observe_wwise_process(resolved_pid)
        resolved_url = waapi.resolve_waapi_url(waapi_url)

        try:
            dcc_version = waapi.get_wwise_version(resolved_url)
        except RuntimeError:
            dcc_version = "unknown"
        identity_after = process_identity.observe_wwise_process(resolved_pid)
        process_identity.require_same_identity(identity_before, identity_after)

        os.environ["DCC_MCP_WWISE_HOST_PID"] = str(resolved_pid)
        os.environ["DCC_MCP_WWISE_WAAPI_URL"] = resolved_url

        execution_bridge = HostExecutionBridge(
            dispatcher=WwiseWaapiDispatcher(),
            default_thread_affinity="any",
            default_execution="sync",
            default_timeout_hint_secs=120,
        )
        options = DccServerOptions.from_env(
            "wwise",
            Path(__file__).resolve().parent / "skills",
            port=port,
            server_name="dcc-mcp-wwise",
            server_version=__version__,
            adapter_version=__version__,
            dcc_version=dcc_version,
            dcc_pid=resolved_pid,
            instance_type="gui",
            host_rpc=resolved_url,
            execution_bridge=execution_bridge,
        )
        super().__init__(options=options)
        self._host_identity = identity_after
        self._waapi_url = resolved_url
        self._readiness = AdapterReadinessBinder(self)
        self._readiness_stop = threading.Event()
        self._readiness_thread: threading.Thread | None = None
        self._menu = WwiseMenu(resolved_url)
        self._set_waapi_readiness(False)

    def start(self, **kwargs: Any) -> Any:
        handle = super().start(**kwargs)
        self._start_readiness_monitor()
        try:
            self._menu.start()
        except RuntimeError as exc:
            _LOGGER.warning("Wwise started without the DCC-MCP menu (%s)", type(exc).__name__)
        return handle

    def stop(self) -> None:
        try:
            self._menu.stop()
        finally:
            self._stop_readiness_monitor()
            super().stop()

    def _set_waapi_readiness(self, ready: bool) -> None:
        self._readiness.mark_dispatcher_ready(
            ready,
            host_execution_bridge_ready=ready,
            main_thread_executor_ready=True,
            dcc_ready=ready,
        )

    def _start_readiness_monitor(self) -> None:
        if self._readiness_thread is not None and self._readiness_thread.is_alive():
            return
        self._readiness_stop.clear()
        self._readiness_thread = threading.Thread(
            target=self._monitor_waapi_readiness,
            name="dcc-mcp-wwise-readiness",
            daemon=False,
        )
        self._readiness_thread.start()

    def _monitor_waapi_readiness(self) -> None:
        while not self._readiness_stop.wait(2.0):
            ready = self.host_identity_is_current() and waapi.is_connected(
                self._waapi_url,
                timeout_secs=0.5,
            )
            self._set_waapi_readiness(ready)

    def host_identity_is_current(self) -> bool:
        """Fail closed if PID, executable, or process start identity changed."""
        try:
            observed = process_identity.observe_wwise_process(self._host_identity.pid)
            process_identity.require_same_identity(self._host_identity, observed)
        except process_identity.ProcessIdentityError:
            return False
        return True

    def _stop_readiness_monitor(self) -> None:
        self._readiness_stop.set()
        thread, self._readiness_thread = self._readiness_thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3.0)
            if thread.is_alive():
                raise RuntimeError("The bounded Wwise readiness monitor did not stop")
        self._set_waapi_readiness(False)

    def _version_string(self) -> str:
        try:
            return waapi.get_wwise_version(self._waapi_url)
        except RuntimeError:
            return "unknown"


def start_server(
    port: int | None = None,
    host_pid: int | None = None,
    waapi_url: str | None = None,
) -> WwiseMcpServer:
    global _server
    if _server is None or not _server.is_running:
        _server = WwiseMcpServer(port=port, host_pid=host_pid, waapi_url=waapi_url)
        _server.register_builtin_actions()
        _server.start()
    return _server


def stop_server() -> None:
    global _server
    if _server is not None:
        _server.stop()
        _server = None


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the DCC-MCP Wwise adapter.")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--host-pid", type=int)
    parser.add_argument("--waapi-url")
    parser.add_argument("--mcp-port", type=int)
    return parser.parse_args(argv)


DEFAULT_WAAPI_URL = waapi.DEFAULT_WAAPI_URL


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(list(argv) if argv is not None else sys.argv[1:])
    try:
        host_pid = _resolve_host_pid(args.host_pid)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    stopped = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: stopped.set())

    try:
        instance = start_server(port=args.mcp_port, host_pid=host_pid, waapi_url=args.waapi_url)
    except process_identity.ProcessIdentityError as exc:
        raise SystemExit("The requested Wwise host identity could not be verified") from exc
    try:
        while not stopped.wait(1.0):
            if not instance.host_identity_is_current():
                break
    finally:
        stop_server()


if __name__ == "__main__":
    main()
