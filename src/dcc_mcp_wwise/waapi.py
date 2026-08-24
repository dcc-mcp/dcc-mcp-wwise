"""Small, bounded wrapper around Audiokinetic's official WAAPI client."""

from __future__ import annotations

import ipaddress
import os
from contextlib import contextmanager
from typing import Any, Iterator, Mapping
from urllib.parse import urlparse

DEFAULT_WAAPI_URL = "ws://127.0.0.1:8080/waapi"


class WaapiConnectionError(RuntimeError):
    """The official client could not establish a WAAPI session."""


class WaapiCallError(RuntimeError):
    """An established WAAPI session rejected or malformed a typed call."""


class WaapiEndpointPolicyError(ValueError):
    """A remote WAAPI endpoint is outside the operator-approved policy."""


def resolve_waapi_url(value: str | None = None) -> str:
    url = (value or os.environ.get("DCC_MCP_WWISE_WAAPI_URL") or DEFAULT_WAAPI_URL).strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"ws", "wss"} or not parsed.hostname or not parsed.port:
        raise ValueError("WAAPI URL must be an absolute ws:// or wss:// URL with a port")
    if parsed.path != "/waapi" or parsed.params or parsed.query or parsed.fragment:
        raise ValueError("WAAPI URL must use the official /waapi path without parameters")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("WAAPI URL must not contain credentials")
    host = parsed.hostname.strip().lower()
    try:
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
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


@contextmanager
def connect_waapi(url: str | None = None) -> Iterator[Any]:
    resolved = resolve_waapi_url(url)
    try:
        client = _client_type()(resolved, allow_exception=True)
    except Exception as exc:
        raise WaapiConnectionError(f"WAAPI connection failed for {resolved}: {exc}") from exc
    try:
        yield client
    except BaseException:
        try:
            client.disconnect()
        except Exception:
            pass
        raise
    else:
        try:
            client.disconnect()
        except Exception as exc:
            raise WaapiCallError(f"WAAPI disconnect failed: {exc}") from exc


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
            raise WaapiCallError(f"WAAPI call failed for {uri}: {exc}") from exc
    if result is None:
        raise WaapiCallError(f"WAAPI call returned no result for {uri}")
    return result


def get_wwise_info(url: str | None = None) -> dict[str, Any]:
    result = call_waapi("ak.wwise.core.getInfo", url=url)
    if not isinstance(result, dict):
        raise WaapiCallError("ak.wwise.core.getInfo returned a non-object result")
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
