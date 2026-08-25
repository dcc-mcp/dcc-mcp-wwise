"""Test-only official-client shape for subprocess deadline regressions."""

from __future__ import annotations

import os
import time
from pathlib import Path


def _delay(phase: str) -> None:
    if os.environ.get("DCC_MCP_WWISE_TEST_PHASE") != phase:
        return
    Path(os.environ["DCC_MCP_WWISE_TEST_READY"]).write_text(phase, encoding="ascii")
    time.sleep(30)


class WaapiClient:
    def __init__(self, _url: str, allow_exception: bool) -> None:
        assert allow_exception is True
        _delay("construction")

    def call(self, _uri: str, _arguments: dict, *, options: dict) -> dict:
        assert options == {}
        _delay("rpc")
        return {"version": {"displayName": "2024.1.0.0"}}

    def disconnect(self) -> None:
        _delay("disconnect")
