"""Fail closed when release metadata and the checked-in uv lock diverge."""

from __future__ import annotations

import json
import re
import stat
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib


ROOT_NAME = "dcc-mcp-wwise"
CORE_SPECIFIER = ">=0.20.14,<1.0.0"
WAAPI_SPECIFIER = ">=0.8.1,<0.9"
WAAPI_VERSION = "0.8.1"
FINAL_VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
VERSION_FILES = (
    "src/dcc_mcp_wwise/__version__.py",
    "src/dcc_mcp_wwise/skills/wwise-project/SKILL.md",
    "src/dcc_mcp_wwise/skills/wwise-audio/SKILL.md",
)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a mapping")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _version(value: Any, label: str) -> str:
    result = _string(value, label)
    if FINAL_VERSION.fullmatch(result) is None:
        raise ValueError(f"{label} must be a final X.Y.Z version")
    return result


def _regular_file(path: Path, label: str) -> None:
    try:
        file_stat = path.lstat()
    except OSError as exc:
        raise ValueError(f"{label} cannot be inspected: {exc.__class__.__name__}") from None
    file_attributes = getattr(file_stat, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if path.is_symlink() or (reparse_flag and file_attributes & reparse_flag):
        raise ValueError(f"{label} must not be a symlink or reparse point")
    if not stat.S_ISREG(file_stat.st_mode):
        raise ValueError(f"{label} must be a regular file")


def _read_toml(path: Path, label: str) -> Mapping[str, Any]:
    _regular_file(path, label)
    try:
        return _mapping(tomllib.loads(path.read_text(encoding="utf-8")), label)
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(
            f"{label} is unreadable or invalid TOML: {exc.__class__.__name__}"
        ) from None


def _read_json(path: Path, label: str) -> Mapping[str, Any]:
    _regular_file(path, label)
    try:
        return _mapping(json.loads(path.read_text(encoding="utf-8")), label)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"{label} is unreadable or invalid JSON: {exc.__class__.__name__}"
        ) from None


def _dependency_map(values: Sequence[Any], label: str) -> dict[str, list[Mapping[str, Any]]]:
    result: dict[str, list[Mapping[str, Any]]] = {}
    for index, value in enumerate(values):
        entry = _mapping(value, f"{label}[{index}]")
        name = _string(entry.get("name"), f"{label}[{index}].name")
        result.setdefault(name, []).append(entry)
    return result


def _locked_package(packages: Sequence[Mapping[str, Any]], name: str) -> Mapping[str, Any]:
    matches = [package for package in packages if package.get("name") == name]
    if len(matches) != 1:
        raise ValueError(f"uv.lock must contain exactly one {name} package")
    return matches[0]


def _validate_version_file(root: Path, relative: str, version: str) -> None:
    path = root / relative
    _regular_file(path, relative)
    text = path.read_text(encoding="utf-8")
    quoted = re.findall(r'version(?:__)?\s*[:=]\s*["\']([^"\']+)["\']', text, re.IGNORECASE)
    if quoted != [version]:
        raise ValueError(f"{relative} must contain exactly one release version matching {version}")


def validate(root: Path) -> None:
    pyproject = _read_toml(root / "pyproject.toml", "pyproject.toml")
    lock = _read_toml(root / "uv.lock", "uv.lock")
    manifest = _read_json(root / ".release-please-manifest.json", "release manifest")
    release_config = _read_json(root / "release-please-config.json", "release config")

    project = _mapping(pyproject.get("project"), "pyproject.toml project")
    if _string(project.get("name"), "project.name") != ROOT_NAME:
        raise ValueError(f"project.name must be {ROOT_NAME!r}")
    project_version = _version(project.get("version"), "project.version")
    dependencies = _list(project.get("dependencies"), "project.dependencies")
    if dependencies.count(f"dcc-mcp-core{CORE_SPECIFIER}") != 1:
        raise ValueError(f"project.dependencies must contain one dcc-mcp-core{CORE_SPECIFIER}")
    if dependencies.count(f"waapi-client{WAAPI_SPECIFIER}") != 1:
        raise ValueError(f"project.dependencies must contain one waapi-client{WAAPI_SPECIFIER}")

    if _version(manifest.get("."), "release manifest root version") != project_version:
        raise ValueError("release manifest version must match project.version")
    packages_config = _mapping(release_config.get("packages"), "release config packages")
    root_config = _mapping(packages_config.get("."), "release config root package")
    if _string(root_config.get("package-name"), "release package-name") != ROOT_NAME:
        raise ValueError(f"release package-name must be {ROOT_NAME!r}")
    extra_files = _list(root_config.get("extra-files"), "release config extra-files")
    extra_paths = {
        _string(_mapping(value, "release extra-file").get("path"), "release extra-file path")
        for value in extra_files
    }
    required_paths = {"pyproject.toml", *VERSION_FILES}
    if not required_paths.issubset(extra_paths):
        raise ValueError("release config must update every version-bearing file")

    raw_packages = _list(lock.get("package"), "uv.lock package")
    lock_packages = [
        _mapping(value, f"uv.lock package[{index}]") for index, value in enumerate(raw_packages)
    ]
    editable_roots = []
    for package in lock_packages:
        source = _mapping(package.get("source"), "uv.lock package.source")
        if source.get("editable") == ".":
            editable_roots.append(package)
    if len(editable_roots) != 1:
        raise ValueError("uv.lock must contain exactly one source.editable='.' root")
    editable_root = editable_roots[0]
    if _string(editable_root.get("name"), "editable root name") != ROOT_NAME:
        raise ValueError(f"editable root must be {ROOT_NAME!r}")
    if _version(editable_root.get("version"), "editable root version") != project_version:
        raise ValueError("editable root version must match project.version")

    core = _locked_package(lock_packages, "dcc-mcp-core")
    core_version = tuple(
        int(part) for part in _version(core.get("version"), "core version").split(".")
    )
    if not ((0, 20, 14) <= core_version < (1, 0, 0)):
        raise ValueError(f"locked dcc-mcp-core is outside {CORE_SPECIFIER}")
    waapi = _locked_package(lock_packages, "waapi-client")
    if _version(waapi.get("version"), "waapi-client version") != WAAPI_VERSION:
        raise ValueError(f"uv.lock must resolve audited waapi-client {WAAPI_VERSION}")

    metadata = _mapping(editable_root.get("metadata"), "editable root metadata")
    requires_dist = _dependency_map(
        _list(metadata.get("requires-dist"), "editable root metadata.requires-dist"),
        "editable root metadata.requires-dist",
    )
    for name, expected in (("dcc-mcp-core", CORE_SPECIFIER), ("waapi-client", WAAPI_SPECIFIER)):
        entries = requires_dist.get(name, [])
        if len(entries) != 1 or entries[0].get("specifier") != expected:
            raise ValueError(f"uv.lock root metadata must require {name}{expected}")

    for relative in VERSION_FILES:
        _validate_version_file(root, relative, project_version)


def main() -> int:
    try:
        validate(Path(__file__).resolve().parents[2])
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"release lock contract failed: {exc}", file=sys.stderr)
        return 1
    print("release lock contract passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
