"""Private process-isolated official WAAPI getInfo probe."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

_MAX_STATUS_BYTES = 64 * 1024
_RUNTIME_VERSION = re.compile(
    r"\A(0|[1-9][0-9]{0,8})\."
    r"(0|[1-9][0-9]{0,8})\."
    r"(0|[1-9][0-9]{0,8})\."
    r"(0|[1-9][0-9]{0,8})\Z"
)


def _write_status(path: Path, record: dict[str, Any]) -> None:
    encoded = json.dumps(record, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > _MAX_STATUS_BYTES:
        encoded = b'{"ok":false,"failure_stage":"protocol","failure_type":"invalid_result"}'
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(encoded)
    os.replace(temporary, path)


def _is_typed_get_info(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    version = result.get("version")
    if not isinstance(version, dict):
        return False
    return any(
        isinstance(version.get(key), str) and _RUNTIME_VERSION.match(version[key]) is not None
        for key in ("displayName", "name")
    )


def execute_probe(
    url: str,
    status_path: Path,
    client_factory: Callable[..., Any] | None = None,
) -> int:
    if client_factory is None:
        from waapi import WaapiClient

        client_factory = WaapiClient

    try:
        client = client_factory(url, allow_exception=True)
    except BaseException:
        _write_status(
            status_path,
            {"ok": False, "failure_stage": "connect", "failure_type": "connection_failed"},
        )
        return 10

    primary: dict[str, Any] | None = None
    result: Any = None
    try:
        try:
            result = client.call("ak.wwise.core.getInfo", {}, options={})
        except BaseException:
            primary = {"ok": False, "failure_stage": "rpc", "failure_type": "rpc_failed"}
            _write_status(status_path, primary)
        else:
            if not _is_typed_get_info(result):
                primary = {
                    "ok": False,
                    "failure_stage": "protocol",
                    "failure_type": "invalid_result",
                }
                _write_status(status_path, primary)
    finally:
        try:
            client.disconnect()
        except BaseException:
            if primary is None:
                primary = {
                    "ok": False,
                    "failure_stage": "disconnect",
                    "failure_type": "cleanup_failed",
                }
                _write_status(status_path, primary)

    if primary is not None:
        return 40
    _write_status(status_path, {"ok": True, "result": result})
    return 0


def main() -> int:
    if len(sys.argv) != 4 or sys.argv[1] not in {"--execute", "--supervise"}:
        return 64
    status_path = Path(sys.argv[2])
    url = sys.argv[3]
    if sys.argv[1] == "--execute":
        return execute_probe(url, status_path)

    child = subprocess.Popen(
        [sys.executable, "-m", __spec__.name, "--execute", str(status_path), url],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    child.wait()
    # Keep the process-group leader / root Job member alive after a terminal
    # record is durable. The parent is the only authority that releases the tree.
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    raise SystemExit(main())
