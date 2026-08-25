from __future__ import annotations

import json
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib


ROOT = Path(__file__).parents[1]


def test_runtime_dependency_uses_the_audited_waapi_client_line() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert "waapi-client>=0.8.1,<0.9" in pyproject["project"]["dependencies"]


def test_checked_in_uv_lock_matches_release_metadata() -> None:
    from scripts.ci.check_uv_lock import validate

    validate(ROOT)


def test_installed_dependency_contract_matches_the_audited_runtime() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/ci/check_installed_dependencies.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def _copy_release_contract(destination: Path) -> None:
    for relative in (
        "pyproject.toml",
        "uv.lock",
        ".release-please-manifest.json",
        "release-please-config.json",
        "src/dcc_mcp_wwise/__version__.py",
        "src/dcc_mcp_wwise/skills/wwise-project/SKILL.md",
        "src/dcc_mcp_wwise/skills/wwise-audio/SKILL.md",
    ):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)


def test_uv_lock_checker_rejects_a_shadow_editable_root(tmp_path: Path) -> None:
    from scripts.ci.check_uv_lock import validate

    _copy_release_contract(tmp_path)
    with (tmp_path / "uv.lock").open("a", encoding="utf-8") as stream:
        stream.write(
            '\n[[package]]\nname = "shadow"\nversion = "0.1.3"\nsource = { editable = "." }\n'
        )

    with pytest.raises(ValueError, match="exactly one source.editable"):
        validate(tmp_path)


def test_uv_lock_checker_rejects_manifest_version_drift(tmp_path: Path) -> None:
    from scripts.ci.check_uv_lock import validate

    _copy_release_contract(tmp_path)
    (tmp_path / ".release-please-manifest.json").write_text(
        json.dumps({".": "0.1.2"}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="manifest version must match"):
        validate(tmp_path)


def test_uv_lock_checker_rejects_malformed_lock_shapes(tmp_path: Path) -> None:
    from scripts.ci.check_uv_lock import validate

    _copy_release_contract(tmp_path)
    (tmp_path / "uv.lock").write_text('package = "not-a-list"\n', encoding="utf-8")

    with pytest.raises(ValueError, match="uv.lock package must be a list"):
        validate(tmp_path)


def test_uv_lock_checker_rejects_a_directory_in_place_of_the_lock(tmp_path: Path) -> None:
    from scripts.ci.check_uv_lock import validate

    _copy_release_contract(tmp_path)
    (tmp_path / "uv.lock").unlink()
    (tmp_path / "uv.lock").mkdir()

    with pytest.raises(ValueError, match="must be a regular file"):
        validate(tmp_path)


def test_uv_lock_checker_rejects_a_windows_reparse_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.ci import check_uv_lock

    _copy_release_contract(tmp_path)
    lock_path = tmp_path / "uv.lock"
    original_lstat = Path.lstat
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

    def fake_lstat(path: Path):
        result = original_lstat(path)
        if path == lock_path:
            return SimpleNamespace(st_mode=result.st_mode, st_file_attributes=reparse_flag)
        return result

    monkeypatch.setattr(Path, "lstat", fake_lstat)

    with pytest.raises(ValueError, match="reparse point"):
        check_uv_lock.validate(tmp_path)


def test_uv_lock_checker_rejects_a_renamed_release_root(tmp_path: Path) -> None:
    from scripts.ci.check_uv_lock import validate

    _copy_release_contract(tmp_path)
    config = json.loads((tmp_path / "release-please-config.json").read_text(encoding="utf-8"))
    config["packages"]["."]["package-name"] = "shadow-root"
    (tmp_path / "release-please-config.json").write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(ValueError, match="release package-name"):
        validate(tmp_path)
