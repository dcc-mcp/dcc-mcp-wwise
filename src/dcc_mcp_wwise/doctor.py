"""Standalone WAAPI doctor orchestration."""

from __future__ import annotations

import os
import re
import sys
from typing import Any
from urllib.parse import urlparse

from . import waapi
from .__version__ import __version__
from .install_contract import (
    EXIT_OK,
    EXIT_PREFLIGHT,
    EXIT_VERIFY,
    SCHEMA_VERSION,
    runtime_core_version,
)

MIN_CORE_VERSION = "0.19.86"
MIN_WWISE_VERSION = "2024.1"
_MAX_VERSION_LENGTH = 39
_RELEASE = re.compile(r"\A(\d{1,9})\.(\d{1,9})(?:\.(\d{1,9})(?:\.\d{1,9})?)?\Z")
_RUNTIME_VERSION = re.compile(
    r"\A(0|[1-9]\d{0,8})\."
    r"(0|[1-9]\d{0,8})\."
    r"(0|[1-9]\d{0,8})\."
    r"(0|[1-9]\d{0,8})\Z"
)


class _RuntimeVersionError(waapi.WaapiCallError):
    """The typed getInfo result did not contain a canonical runtime version."""


def _release_tuple(value: str) -> tuple[int, int, int] | None:
    normalized = value.strip()
    if len(normalized) > _MAX_VERSION_LENGTH:
        return None
    match = _RELEASE.match(normalized)
    if match is None:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch or 0)


def _runtime_version_tuple(value: str) -> tuple[int, int, int, int] | None:
    if len(value) > _MAX_VERSION_LENGTH:
        return None
    match = _RUNTIME_VERSION.match(value)
    if match is None:
        return None
    return tuple(int(component) for component in match.groups())


def _typed_wwise_version(
    info: Any,
) -> tuple[str, tuple[int, int, int, int]]:
    if not isinstance(info, dict):
        raise waapi.WaapiCallError("ak.wwise.core.getInfo returned a non-object result")
    version = info.get("version")
    if isinstance(version, dict):
        for key in ("displayName", "name"):
            value = version.get(key)
            if not isinstance(value, str):
                continue
            release = _runtime_version_tuple(value)
            if release is not None:
                return value, release
    raise _RuntimeVersionError("ak.wwise.core.getInfo did not return a valid Wwise version")


def _report(
    checks: dict[str, Any],
    steps: list[dict[str, Any]],
    exit_code: int,
    failure_stage: str | None = None,
    failure_reason: str | None = None,
    next_steps: list[dict[str, Any]] | None = None,
    verb: str = "doctor",
) -> dict[str, Any]:
    directly_usable = exit_code == EXIT_OK
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok" if directly_usable else "failed",
        "dcc_type": "wwise",
        "verb": verb,
        "adapter_version": __version__,
        "core_version": runtime_core_version(),
        "checks": checks,
        "steps": steps,
        "next_steps": list(next_steps or ()),
        "receipt_path": None,
        "verify": {
            "directly_usable": directly_usable,
            "failure_stage": failure_stage,
            "failure_reason": failure_reason,
        },
        "_exit_code": exit_code,
    }


def doctor_report(url: str | None = None, verb: str = "doctor") -> dict[str, Any]:
    try:
        resolved = waapi.resolve_waapi_url(url)
    except waapi.WaapiEndpointPolicyError as exc:
        attempted = (
            url or os.environ.get("DCC_MCP_WWISE_WAAPI_URL") or waapi.DEFAULT_WAAPI_URL
        ).strip()
        parsed = urlparse(attempted)
        try:
            port = parsed.port
        except ValueError:
            port = None
        reason = str(exc)
        checks = {
            "endpoint": {
                "success": False,
                "url": attempted,
                "host": parsed.hostname,
                "port": port,
                "allowed": False,
            }
        }
        return _report(
            checks,
            [{"id": "validate-endpoint", "status": "failed", "message": reason}],
            EXIT_PREFLIGHT,
            "endpoint_allowlist",
            reason,
            [
                {
                    "id": "use-loopback-waapi",
                    "description": "Retry the default loopback WAAPI endpoint",
                    "command": [
                        "dcc-mcp-wwise",
                        verb,
                        "--json",
                        "--waapi-url",
                        waapi.DEFAULT_WAAPI_URL,
                    ],
                    "why": reason,
                }
            ],
            verb=verb,
        )
    except ValueError as exc:
        reason = "Invalid WAAPI endpoint configuration: %s" % exc
        checks = {
            "endpoint": {
                "success": False,
                "url": None,
                "host": None,
                "port": None,
                "allowed": False,
            }
        }
        return _report(
            checks,
            [{"id": "validate-endpoint", "status": "failed", "message": reason}],
            EXIT_PREFLIGHT,
            "configuration",
            reason,
            [
                {
                    "id": "use-loopback-waapi",
                    "description": "Retry the default loopback WAAPI endpoint",
                    "command": [
                        "dcc-mcp-wwise",
                        verb,
                        "--json",
                        "--waapi-url",
                        waapi.DEFAULT_WAAPI_URL,
                    ],
                    "why": reason,
                }
            ],
            verb=verb,
        )
    parsed = urlparse(resolved)
    host = str(parsed.hostname)
    endpoint = {
        "success": True,
        "url": resolved,
        "host": host,
        "port": parsed.port,
        "allowed": True,
    }
    checks: dict[str, Any] = {"endpoint": endpoint}
    steps: list[dict[str, Any]] = [{"id": "validate-endpoint", "status": "ok"}]

    core_version = runtime_core_version()
    core_release = _release_tuple(core_version)
    minimum_core = _release_tuple(MIN_CORE_VERSION)
    core_compatible = (
        core_release is not None and minimum_core is not None and core_release >= minimum_core
    )
    checks["core"] = {
        "success": core_compatible,
        "version": core_version,
        "minimum": MIN_CORE_VERSION,
    }
    if not core_compatible:
        reason = "dcc-mcp-core %s is unsupported; %s or newer is required" % (
            core_version,
            MIN_CORE_VERSION,
        )
        steps.append({"id": "validate-core", "status": "failed", "message": reason})
        return _report(
            checks,
            steps,
            EXIT_PREFLIGHT,
            "core",
            reason,
            [
                {
                    "id": "upgrade-core",
                    "description": "Upgrade dcc-mcp-core in the adapter environment",
                    "command": [
                        sys.executable,
                        "-m",
                        "pip",
                        "install",
                        "--upgrade",
                        "dcc-mcp-core>=%s" % MIN_CORE_VERSION,
                    ],
                    "why": reason,
                }
            ],
            verb=verb,
        )
    steps.append({"id": "validate-core", "status": "ok"})

    runtime_version_reason: str | None = None
    try:
        runtime_version = waapi.call_waapi(
            "ak.wwise.core.getInfo",
            url=resolved,
            result_validator=_typed_wwise_version,
        )
    except _RuntimeVersionError as exc:
        runtime_version = None
        runtime_version_reason = str(exc)
    except waapi.WaapiConnectionError as exc:
        reason = str(exc)
        checks["runtime"] = {
            "success": False,
            "waapi_enabled": False,
            "client_allowed": None,
            "reason": reason,
        }
        steps.append({"id": "probe-waapi", "status": "failed", "message": reason})
        return _report(
            checks,
            steps,
            EXIT_PREFLIGHT,
            "waapi_enablement",
            reason,
            [
                {
                    "id": "retry-waapi-doctor",
                    "description": "Enable WAAPI in Wwise, then retry the typed probe",
                    "command": ["dcc-mcp-wwise", verb, "--json", "--waapi-url", resolved],
                    "why": reason,
                }
            ],
            verb=verb,
        )
    except RuntimeError as exc:
        reason = str(exc)
        checks["runtime"] = {
            "success": False,
            "waapi_enabled": True,
            "client_allowed": True,
            "reason": reason,
        }
        steps.append({"id": "probe-waapi", "status": "failed", "message": reason})
        return _report(
            checks,
            steps,
            EXIT_VERIFY,
            "runtime",
            reason,
            [
                {
                    "id": "retry-waapi-doctor",
                    "description": "Retry the typed WAAPI runtime probe",
                    "command": ["dcc-mcp-wwise", verb, "--json", "--waapi-url", resolved],
                    "why": reason,
                }
            ],
            verb=verb,
        )

    wwise_version = runtime_version[0] if runtime_version is not None else "unknown"
    wwise_release = runtime_version[1][:3] if runtime_version is not None else None
    minimum_wwise = _release_tuple(MIN_WWISE_VERSION)
    checks["runtime"] = {
        "success": False,
        "waapi_enabled": True,
        "client_allowed": True,
        "wwise_version": wwise_version,
        "minimum_wwise_version": MIN_WWISE_VERSION,
    }
    if wwise_release is None:
        reason = runtime_version_reason or "ak.wwise.core.getInfo returned invalid version data"
        steps.append({"id": "probe-waapi", "status": "failed", "message": reason})
        return _report(
            checks,
            steps,
            EXIT_VERIFY,
            "runtime",
            reason,
            [
                {
                    "id": "retry-waapi-doctor",
                    "description": "Retry the typed WAAPI runtime probe",
                    "command": ["dcc-mcp-wwise", verb, "--json", "--waapi-url", resolved],
                    "why": reason,
                }
            ],
            verb=verb,
        )
    runtime_compatible = minimum_wwise is not None and wwise_release >= minimum_wwise
    checks["runtime"]["success"] = runtime_compatible
    if not runtime_compatible:
        reason = "Wwise %s is unsupported; %s or newer is required" % (
            wwise_version,
            MIN_WWISE_VERSION,
        )
        steps.append({"id": "probe-waapi", "status": "failed", "message": reason})
        return _report(
            checks,
            steps,
            EXIT_PREFLIGHT,
            "wwise_version",
            reason,
            [
                {
                    "id": "upgrade-wwise",
                    "description": "Upgrade Wwise through Audiokinetic Launcher",
                    "command": ["dcc-mcp-wwise", verb, "--json", "--waapi-url", resolved],
                    "why": reason,
                }
            ],
            verb=verb,
        )
    steps.append({"id": "probe-waapi", "status": "ok"})
    return _report(checks, steps, EXIT_OK, verb=verb)
