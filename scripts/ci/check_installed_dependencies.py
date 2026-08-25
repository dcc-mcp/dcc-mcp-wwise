"""Verify the installed Core and WAAPI client stay inside the supported contract."""

from __future__ import annotations

import sys
from importlib import metadata

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

CORE_SPECIFIER = SpecifierSet(">=0.20.14,<1.0.0")
WAAPI_VERSION = Version("0.8.1")


def _runtime_requirement(name: str) -> Requirement:
    requirements = [
        Requirement(value)
        for value in metadata.requires("dcc-mcp-wwise") or []
        if Requirement(value).name == name and Requirement(value).marker is None
    ]
    if len(requirements) != 1:
        raise ValueError(f"adapter metadata must contain one runtime requirement for {name}")
    return requirements[0]


def validate() -> None:
    core = Version(metadata.version("dcc-mcp-core"))
    waapi = Version(metadata.version("waapi-client"))
    if core not in CORE_SPECIFIER:
        raise ValueError(f"installed dcc-mcp-core {core} is outside {CORE_SPECIFIER}")
    if waapi != WAAPI_VERSION:
        raise ValueError(f"installed waapi-client must be exactly {WAAPI_VERSION}, got {waapi}")

    core_requirement = _runtime_requirement("dcc-mcp-core")
    waapi_requirement = _runtime_requirement("waapi-client")
    if core_requirement.specifier != CORE_SPECIFIER:
        raise ValueError("adapter metadata has an unexpected dcc-mcp-core specifier")
    if str(waapi_requirement.specifier) not in {">=0.8.1,<0.9", "<0.9,>=0.8.1"}:
        raise ValueError("adapter metadata must bind waapi-client to >=0.8.1,<0.9")


def main() -> int:
    try:
        validate()
    except (metadata.PackageNotFoundError, ValueError) as exc:
        print(f"installed dependency contract failed: {exc}", file=sys.stderr)
        return 1
    print("installed dependency contract passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
