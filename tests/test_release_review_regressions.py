from __future__ import annotations

import shlex
import subprocess
from glob import glob
from pathlib import Path
from shutil import copy2

import pytest
from dcc_mcp_core import yaml_loads
from twine.exceptions import InvalidDistribution
from twine.package import PackageFile

ROOT = Path(__file__).resolve().parents[1]
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
LOCK_SYNC_WORKFLOW = ROOT / ".github" / "workflows" / "release-please-lock-sync.yml"


def _load(path: Path):
    return yaml_loads(path.read_text(encoding="utf-8"))


def _run_workflow_guard(run: str, expected: str, environment: dict[str, str]) -> int:
    command = next(
        (line.strip() for line in run.splitlines() if line.strip() == expected),
        "true",
    )
    for name, value in environment.items():
        command = command.replace(f'"${name}"', shlex.quote(value))
    completed = subprocess.run(
        ["bash", "-c", command],
        check=False,
    )
    return completed.returncode


def test_asset_publication_stops_on_a_mid_loop_tag_move() -> None:
    workflow = _load(RELEASE_WORKFLOW)
    attach_run = workflow["jobs"]["attach-release-assets"]["steps"][1]["run"]
    asset_loop = attach_run.rsplit("for asset in release-assets/*; do", 1)[-1]
    guard = 'test "$TAG_SHA" = "$VERIFIED_SOURCE_SHA"'
    verified = "a" * 40
    uploads: list[str] = []

    for asset, current_tag in (
        ("adapter.whl", verified),
        ("adapter.tar.gz", "b" * 40),
        ("SHA256SUMS", "b" * 40),
    ):
        if _run_workflow_guard(
            asset_loop,
            guard,
            {"TAG_SHA": current_tag, "VERIFIED_SOURCE_SHA": verified},
        ):
            break
        uploads.append(asset)

    assert uploads == ["adapter.whl"]
    tag_lookup = (
        "TAG_SHA=$(gh api \"repos/$GITHUB_REPOSITORY/git/ref/tags/$TAG_NAME\" --jq '.object.sha')"
    )
    post = "releases/$CURRENT_RELEASE_ID/assets?name=$ENCODED_NAME"
    assert asset_loop.index(tag_lookup) < asset_loop.index(guard) < asset_loop.index(post)


def test_publishers_reject_mismatched_server_artifact_digest() -> None:
    workflow = _load(RELEASE_WORKFLOW)
    expected_digest = "sha256:" + "a" * 64
    mismatched_digest = "sha256:" + "b" * 64
    guard = 'test "$CURRENT_ARTIFACT_DIGEST" = "$ARTIFACT_DIGEST"'
    metadata_lookup = (
        'CURRENT_ARTIFACT_JSON=$(gh api "repos/$GITHUB_REPOSITORY/actions/artifacts/$ARTIFACT_ID")'
    )

    for job_name in ("publish", "attach-release-assets"):
        identity_run = workflow["jobs"][job_name]["steps"][1]["run"]
        assert (
            _run_workflow_guard(
                identity_run,
                guard,
                {
                    "ARTIFACT_DIGEST": expected_digest,
                    "CURRENT_ARTIFACT_DIGEST": mismatched_digest,
                },
            )
            != 0
        )
        assert metadata_lookup in identity_run
        assert identity_run.index(metadata_lookup) < identity_run.index(guard)


def test_pypi_action_glob_contains_only_distributions(tmp_path: Path) -> None:
    workflow = _load(RELEASE_WORKFLOW)
    publish = workflow["jobs"]["publish"]
    download = publish["steps"][0]
    publication = publish["steps"][-1]
    bundle = tmp_path / download["with"]["path"]
    packages = tmp_path / publication["with"]["packages-dir"]
    bundle.mkdir(parents=True)
    for name in (
        "dcc_mcp_wwise-1.0.0-py3-none-any.whl",
        "dcc_mcp_wwise-1.0.0.tar.gz",
        "SHA256SUMS",
    ):
        (bundle / name).touch()

    with pytest.raises(InvalidDistribution, match="Unknown distribution format"):
        PackageFile.from_filename(str(bundle / "SHA256SUMS"), None)

    identity_run = publish["steps"][1]["run"]
    assert "cp release-bundle/*.whl release-bundle/*.tar.gz pypi-dist/" in identity_run
    packages.mkdir()
    for pattern in ("*.whl", "*.tar.gz"):
        for distribution in bundle.glob(pattern):
            copy2(distribution, packages / distribution.name)
    action_equivalent_glob = glob(str(packages / "*"))
    assert action_equivalent_glob
    assert all(path.endswith((".whl", ".tar.gz")) for path in action_equivalent_glob)
    assert download["with"]["path"] != publication["with"]["packages-dir"]
    assert "dist/SHA256SUMS" in workflow["jobs"]["build"]["steps"][-2]["with"]["path"]


def test_downloaded_release_artifacts_are_hash_verified_fail_closed_before_publish() -> None:
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    download = workflow.index("actions/download-artifact")
    pypi_publish = workflow.index("pypa/gh-action-pypi-publish", download)
    github_publish = workflow.index(
        "releases/$CURRENT_RELEASE_ID/assets?name=$ENCODED_NAME", download
    )
    verification = workflow.find("sha256sum --check", download)

    assert verification != -1, "downloaded files must be rehashed against trusted per-file digests"
    assert verification < min(pypi_publish, github_publish)

    verification_block = workflow[verification : min(pypi_publish, github_publish)]
    assert "continue-on-error: true" not in verification_block
    assert "::warning" not in verification_block
    assert "|| true" not in verification_block


def test_lock_generation_is_credential_free_and_write_token_is_push_only() -> None:
    workflow = _load(LOCK_SYNC_WORKFLOW)
    generation = workflow["jobs"]["generate-release-lock"]
    sync = workflow["jobs"]["sync-release-lock"]
    checkout = generation["steps"][0]
    regenerate = next(
        step
        for step in generation["steps"]
        if step.get("name") == "Regenerate the exact release lock"
    )
    push = next(
        step for step in sync["steps"] if step.get("name") == "Push the recaptured lock commit"
    )

    assert generation["permissions"]["contents"] == "read"
    assert checkout["with"]["persist-credentials"] is False
    assert "token" not in checkout["with"]
    assert "env" not in regenerate
    assert "PERSONAL_ACCESS_TOKEN" not in regenerate["run"]
    assert push["env"] == {
        "HEAD_REF": "${{ github.event.pull_request.head.ref }}",
        "WRITE_TOKEN": "${{ secrets.PERSONAL_ACCESS_TOKEN }}",
    }
    executable = [line.strip() for line in push["run"].splitlines() if line.strip()]
    assert executable[0] == "set -euo pipefail"
    assert executable[-1].startswith("/usr/bin/git ")
    assert sum("push" in line for line in executable) == 1


def test_github_assets_require_successful_pypi_publication() -> None:
    workflow = _load(RELEASE_WORKFLOW)
    attach = workflow["jobs"]["attach-release-assets"]

    assert "publish" in attach["needs"]
    assert "needs.publish.result == 'success'" in attach["if"]


def test_release_identity_freezes_id_and_state_and_recaptures_before_mutations() -> None:
    workflow = _load(RELEASE_WORKFLOW)
    verify = workflow["jobs"]["verify-release-source"]
    publish = workflow["jobs"]["publish"]
    attach = workflow["jobs"]["attach-release-assets"]

    assert set(verify["outputs"]) >= {
        "release_id",
        "release_draft",
        "release_prerelease",
        "release_immutable",
    }
    for job in (publish, attach):
        identity = job["steps"][1]
        assert identity["env"]["VERIFIED_RELEASE_ID"] == (
            "${{ needs.verify-release-source.outputs.release_id }}"
        )
        assert "CURRENT_RELEASE_JSON=$(gh api" in identity["run"]
        assert "CURRENT_RELEASE_ID=$(jq" in identity["run"]
        assert 'test "$CURRENT_RELEASE_ID" = "$VERIFIED_RELEASE_ID"' in identity["run"]
        assert "CURRENT_RELEASE_DRAFT=$(jq" in identity["run"]
        assert "CURRENT_RELEASE_PRERELEASE=$(jq" in identity["run"]
        assert "CURRENT_RELEASE_IMMUTABLE=$(jq" in identity["run"]

    attach_run = attach["steps"][1]["run"]
    assert "gh release upload" not in attach_run
    upload = attach_run.index("releases/$CURRENT_RELEASE_ID/assets?name=")
    assert '--input "$asset"' in attach_run[upload:]
    assert attach_run.rfind("CURRENT_RELEASE_JSON=$(gh api", 0, upload) > attach_run.index("done")
