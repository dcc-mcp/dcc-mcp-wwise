"""Standalone WAAPI doctor orchestration."""

from __future__ import annotations

import os
import re
import sys
import time
from typing import Any
from urllib.parse import urlparse

import dcc_mcp_core as _core
from dcc_mcp_core.deployment import (
    INSTALL_EXIT_OK as EXIT_OK,
)
from dcc_mcp_core.deployment import (
    INSTALL_EXIT_PREFLIGHT as EXIT_PREFLIGHT,
)
from dcc_mcp_core.deployment import (
    INSTALL_EXIT_VERIFY as EXIT_VERIFY,
)
from dcc_mcp_core.deployment import (
    INSTALL_SOP_SCHEMA_VERSION as SCHEMA_VERSION,
)

from . import process_identity, waapi
from .__version__ import __version__

MIN_CORE_VERSION = "0.20.14"
MIN_WWISE_VERSION = "2024.1"
_MAX_VERSION_LENGTH = 39
_RELEASE = re.compile(r"\A([0-9]{1,9})\.([0-9]{1,9})(?:\.([0-9]{1,9})(?:\.[0-9]{1,9})?)?\Z")
_RUNTIME_VERSION = re.compile(
    r"\A(0|[1-9][0-9]{0,8})\."
    r"(0|[1-9][0-9]{0,8})\."
    r"(0|[1-9][0-9]{0,8})\."
    r"(0|[1-9][0-9]{0,8})\Z"
)


class _RuntimeVersionError(waapi.WaapiCallError):
    """The typed getInfo result did not contain a canonical runtime version."""

    def __init__(self) -> None:
        super().__init__(
            "The WAAPI probe returned an invalid result",
            failure_stage="protocol",
            failure_type="invalid_result",
        )


def _public_waapi_reason(exc: waapi.WaapiCallError) -> str:
    return {
        "connection_failed": "The WAAPI connection could not be established",
        "rpc_failed": "The WAAPI getInfo RPC failed",
        "cleanup_failed": "The WAAPI probe cleanup failed",
        "invalid_result": "The WAAPI probe returned an invalid result",
        "missing_result": "The WAAPI probe returned no result",
        "timeout": "The WAAPI probe exceeded its deadline",
    }.get(exc.failure_type, "The WAAPI probe failed")


def runtime_core_version() -> str:
    """Return the version exposed by the required formal Core runtime."""
    return str(_core.__version__)


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
        raise _RuntimeVersionError()
    version = info.get("version")
    if isinstance(version, dict):
        for key in ("displayName", "name"):
            value = version.get(key)
            if not isinstance(value, str):
                continue
            release = _runtime_version_tuple(value)
            if release is not None:
                return value, release
    raise _RuntimeVersionError()


def _report(
    checks: dict[str, Any],
    steps: list[dict[str, Any]],
    exit_code: int,
    failure_stage: str | None = None,
    failure_reason: str | None = None,
    next_steps: list[dict[str, Any]] | None = None,
    verb: str = "doctor",
    failure_type: str | None = None,
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
            "failure_type": failure_type,
        },
        "_exit_code": exit_code,
    }


def doctor_report(
    url: str | None = None,
    verb: str = "doctor",
    host_pid: int | None = None,
    timeout_ms: int = 5000,
) -> dict[str, Any]:
    if (
        not isinstance(timeout_ms, int)
        or isinstance(timeout_ms, bool)
        or not 1 <= timeout_ms <= 120000
    ):
        reason = "timeout_ms must be an integer from 1 through 120000"
        return _report(
            {},
            [{"id": "validate-timeout", "status": "failed", "message": reason}],
            EXIT_PREFLIGHT,
            "configuration",
            reason,
            verb=verb,
            failure_type="invalid_timeout",
        )
    deadline = time.monotonic() + (timeout_ms / 1000.0)
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

    identity_before: process_identity.WwiseProcessIdentity | None = None
    if waapi.is_loopback_waapi_url(resolved) and host_pid is not None:
        try:
            identity_before = process_identity.observe_wwise_process(host_pid)
        except process_identity.ProcessIdentityError as exc:
            reason = "The requested Wwise host identity could not be verified"
            checks["identity"] = {"success": False, "pid": host_pid}
            steps.append({"id": "bind-host-identity", "status": "failed", "message": reason})
            return _report(
                checks,
                steps,
                EXIT_PREFLIGHT,
                "host_identity",
                reason,
                verb=verb,
                failure_type=exc.failure_type,
            )
        checks["identity"] = identity_before.as_public_check(success=False)

    runtime_version_reason: str | None = None
    runtime_version_failure_type: str | None = None
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise waapi.WaapiTimeoutError()
        runtime_version = _typed_wwise_version(
            waapi.get_wwise_info(resolved, timeout_secs=remaining)
        )
    except _RuntimeVersionError as exc:
        runtime_version = None
        runtime_version_reason = _public_waapi_reason(exc)
        runtime_version_failure_type = exc.failure_type
    except waapi.WaapiConnectionError as exc:
        reason = _public_waapi_reason(exc)
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
            failure_type=exc.failure_type,
        )
    except waapi.WaapiCallError as exc:
        reason = _public_waapi_reason(exc)
        failure_stage = "deadline" if exc.failure_type == "timeout" else "runtime"
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
            failure_stage,
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
            failure_type=exc.failure_type,
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
            failure_type=runtime_version_failure_type,
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
    if not waapi.is_loopback_waapi_url(resolved):
        reason = (
            "Remote WAAPI verification is preflight-only; directly usable requires the "
            "adapter to run on the Wwise authoring host with local PID binding"
        )
        steps.append({"id": "bind-host", "status": "failed", "message": reason})
        return _report(
            checks,
            steps,
            EXIT_PREFLIGHT,
            "host_binding",
            reason,
            [
                {
                    "id": "start-adapter-on-wwise-host",
                    "description": "Start the PID-bound adapter on the Wwise authoring host",
                    "command": [
                        "dcc-mcp-wwise",
                        "--waapi-url",
                        waapi.DEFAULT_WAAPI_URL,
                    ],
                    "why": reason,
                    "execution_host": "wwise_host",
                }
            ],
            verb=verb,
            failure_type="identity_unavailable",
        )
    if host_pid is None:
        reason = (
            "Loopback WAAPI is reachable, but no exact Wwise PID/executable/start identity "
            "was supplied and independently observed"
        )
        checks["identity"] = {"success": False, "observed": False}
        steps.append({"id": "bind-host-identity", "status": "failed", "message": reason})
        return _report(
            checks,
            steps,
            EXIT_PREFLIGHT,
            "host_identity",
            reason,
            [
                {
                    "id": "supply-wwise-pid",
                    "description": "Retry with the exact local Wwise Authoring PID",
                    "command": [
                        "dcc-mcp-wwise",
                        verb,
                        "--json",
                        "--host-pid",
                        "PID",
                    ],
                    "why": reason,
                }
            ],
            verb=verb,
            failure_type="identity_unavailable",
        )
    assert identity_before is not None
    try:
        identity_after = process_identity.observe_wwise_process(host_pid)
        process_identity.require_same_identity(identity_before, identity_after)
    except process_identity.ProcessIdentityError as exc:
        reason = "The Wwise host identity changed during the WAAPI probe"
        checks["identity"] = identity_before.as_public_check(success=False)
        steps.append({"id": "recapture-host-identity", "status": "failed", "message": reason})
        return _report(
            checks,
            steps,
            EXIT_PREFLIGHT,
            "host_identity",
            reason,
            verb=verb,
            failure_type=exc.failure_type,
        )
    if time.monotonic() > deadline:
        reason = "The Wwise verification exceeded its deadline"
        checks["identity"] = identity_after.as_public_check(success=False)
        steps.append({"id": "enforce-deadline", "status": "failed", "message": reason})
        return _report(
            checks,
            steps,
            EXIT_VERIFY,
            "deadline",
            reason,
            verb=verb,
            failure_type="timeout",
        )
    checks["identity"] = identity_after.as_public_check(success=True)
    steps.append({"id": "recapture-host-identity", "status": "ok"})
    return _report(checks, steps, EXIT_OK, verb=verb)
