from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from dcc_mcp_core import yaml_loads

ROOT = Path(__file__).parents[1]


def test_workflow_contract_checker_accepts_the_release_dag() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/ci/check_workflows.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def _release_document():
    return yaml_loads((ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8"))


def test_release_contract_ignores_comment_decoys_but_rejects_real_clobber() -> None:
    from scripts.ci.check_workflows import validate_release

    source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    source += "\n# inert example: gh release upload --clobber\n"
    document = yaml_loads(source)
    attach = document["jobs"]["attach-release-assets"]["steps"][-1]
    attach["run"] += " --clobber"

    with pytest.raises(ValueError, match="clobber"):
        validate_release(document)


def test_release_contract_rejects_a_second_github_mutation() -> None:
    from scripts.ci.check_workflows import validate_release

    document = _release_document()
    document["jobs"]["attach-release-assets"]["steps"].insert(
        0,
        {
            "name": "Decoy upload",
            "run": 'gh release upload "$TAG_NAME" dist/* --repo "$GITHUB_REPOSITORY"',
        },
    )

    with pytest.raises(ValueError, match="exactly one GitHub Release upload mutation"):
        validate_release(document)


def test_release_contract_rejects_a_second_pypi_mutation() -> None:
    from scripts.ci.check_workflows import validate_release

    document = _release_document()
    publish = document["jobs"]["publish"]["steps"][-1]
    document["jobs"]["publish"]["steps"].append(dict(publish))

    with pytest.raises(ValueError, match="exactly one PyPI publication mutation"):
        validate_release(document)


def test_release_consumers_are_bound_to_the_build_artifact_id() -> None:
    from scripts.ci.check_workflows import validate_release

    document = _release_document()
    document["jobs"]["publish"]["steps"][0]["with"]["artifact-ids"] = "1"

    with pytest.raises(ValueError, match="exact build artifact ID"):
        validate_release(document)


def test_release_rejects_an_indirect_github_upload_in_the_authoritative_step() -> None:
    from scripts.ci.check_workflows import validate_release

    document = _release_document()
    attach = document["jobs"]["attach-release-assets"]["steps"][-1]
    attach["run"] += '\nG=gh; R=release; U=upload; $G $R $U "$TAG_NAME" release-assets/*\n'

    with pytest.raises(ValueError, match="closed mutation surface"):
        validate_release(document)


def test_release_rejects_a_decoy_pypi_upload_in_the_identity_step() -> None:
    from scripts.ci.check_workflows import validate_release

    document = _release_document()
    identity = document["jobs"]["publish"]["steps"][1]
    identity["run"] += "\npython -m twine upload dist/*\n"

    with pytest.raises(ValueError, match="closed mutation surface"):
        validate_release(document)
