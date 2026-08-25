from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _wait_for(path: Path) -> None:
    deadline = time.monotonic() + 3.0
    while not path.is_file():
        if time.monotonic() >= deadline:
            raise RuntimeError("descendant did not become ready")
        time.sleep(0.01)


def main() -> None:
    if sys.argv[1] == "--child":
        Path(sys.argv[2]).write_text(str(os.getpid()), encoding="ascii")
        while True:
            time.sleep(0.05)

    pid_path, descendant_path, ready_path = map(Path, sys.argv[1:4])
    subprocess.Popen(
        [sys.executable, __file__, "--child", str(descendant_path)],
        stdin=subprocess.DEVNULL,
        close_fds=True,
    )
    _wait_for(descendant_path)
    pid_path.write_text(str(os.getpid()), encoding="ascii")
    ready_path.write_text("ready", encoding="ascii")
    if len(sys.argv) == 5:
        status_path = Path(sys.argv[4])
        status_path.write_text(
            json.dumps(
                {
                    "ok": True,
                    "result": {"version": {"displayName": "2024.1.0.0"}},
                }
            ),
            encoding="utf-8",
        )
    while True:
        time.sleep(0.05)


if __name__ == "__main__":
    main()
