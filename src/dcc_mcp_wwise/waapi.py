"""Small, bounded wrapper around Audiokinetic's official WAAPI client."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator, Mapping
from urllib.parse import urlparse

DEFAULT_WAAPI_URL = "ws://127.0.0.1:8080/waapi"


def resolve_waapi_url(value: str | None = None) -> str:
    url = (value or os.environ.get("DCC_MCP_WWISE_WAAPI_URL") or DEFAULT_WAAPI_URL).strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"ws", "wss"} or not parsed.hostname or not parsed.port:
        raise ValueError("WAAPI URL must be an absolute ws:// or wss:// URL with a port")
    return url


def _client_type():
    from waapi import WaapiClient

    return WaapiClient


@contextmanager
def connect_waapi(url: str | None = None) -> Iterator[Any]:
    resolved = resolve_waapi_url(url)
    try:
        client = _client_type()(resolved, allow_exception=True)
    except Exception as exc:
        raise RuntimeError(f"WAAPI connection failed for {resolved}: {exc}") from exc
    try:
        yield client
    finally:
        client.disconnect()


def call_waapi(
    uri: str,
    arguments: Mapping[str, Any] | None = None,
    *,
    options: Mapping[str, Any] | None = None,
    url: str | None = None,
) -> Any:
    with connect_waapi(url) as client:
        try:
            result = client.call(uri, dict(arguments or {}), options=dict(options or {}))
        except Exception as exc:
            raise RuntimeError(f"WAAPI call failed for {uri}: {exc}") from exc
    if result is None:
        raise RuntimeError(f"WAAPI call returned no result for {uri}")
    return result


def get_wwise_info(url: str | None = None) -> dict[str, Any]:
    result = call_waapi("ak.wwise.core.getInfo", url=url)
    if not isinstance(result, dict):
        raise RuntimeError("ak.wwise.core.getInfo returned a non-object result")
    return result


def get_wwise_version(url: str | None = None) -> str:
    version = get_wwise_info(url).get("version", "unknown")
    if isinstance(version, dict):
        return str(version.get("displayName") or version.get("name") or "unknown")
    return str(version)


def is_connected(url: str | None = None) -> bool:
    try:
        get_wwise_info(url)
    except (RuntimeError, ValueError):
        return False
    return True
