from __future__ import annotations

import base64
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
RECOVERY_WORKFLOW = RELEASE_WORKFLOW
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


def _run_digest_normalizer(
    run: str, value: str, expected: str
) -> subprocess.CompletedProcess[bytes]:
    marker = 'ARTIFACT_SHA256=$(canonical_artifact_sha256 "$ARTIFACT_DIGEST")'
    _, separator, _ = run.partition(marker)
    assert separator, "publisher must canonicalize the build artifact digest"
    function_start = run.index("canonical_artifact_sha256() {")
    function_end = run.index("\n}\n", function_start) + 3
    assertion = 'normalized=$(canonical_artifact_sha256 {}); test "$normalized" = {}\n'.format(
        shlex.quote(value), shlex.quote(expected)
    )
    script = run[function_start:function_end] + "\n" + assertion
    script = script.replace("\r", "")
    encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")
    return subprocess.run(
        ["bash", "-c", f"printf %s {encoded} | base64 -d | bash"],
        check=False,
        capture_output=True,
    )


def test_publishers_canonicalize_bare_and_prefixed_sha256_artifact_digests() -> None:
    workflow = _load(RELEASE_WORKFLOW)
    digest = "a" * 64

    for job_name in ("publish", "attach-release-assets"):
        identity_run = workflow["jobs"][job_name]["steps"][1]["run"]
        for value in (digest, f"sha256:{digest}"):
            completed = _run_digest_normalizer(identity_run, value, digest)
            assert completed.returncode == 0


@pytest.mark.parametrize(
    "value",
    [
        "",
        "a" * 63,
        "a" * 65,
        "sha512:" + "a" * 64,
        "sha256:" + "A" * 64,
        "sha256:" + "a" * 64 + " ",
        " sha256:" + "a" * 64,
        "sha256:" + "g" * 64,
    ],
)
def test_publishers_reject_noncanonical_sha256_artifact_identities(value: str) -> None:
    workflow = _load(RELEASE_WORKFLOW)
    digest = "a" * 64

    for job_name in ("publish", "attach-release-assets"):
        identity_run = workflow["jobs"][job_name]["steps"][1]["run"]
        assert _run_digest_normalizer(identity_run, value, digest).returncode != 0


def test_recovery_dispatch_is_frozen_to_the_v013_incident_and_rebuilds_the_tag() -> None:
    workflow = _load(RECOVERY_WORKFLOW)
    dispatch = workflow["on"]["workflow_dispatch"]
    expected_defaults = {
        "tag": "v0.1.3",
        "source_sha": "d921113c14ec1c270897b70d553d1261d7a20fa1",
        "release_id": "377552005",
        "original_run_id": "33037251075",
        "original_artifact_id": "9632474230",
        "original_artifact_digest": (
            "9e28fd0352291399a8499dea12680b2b0b7c56d869e9e1756bdf72a96ca9806c"
        ),
        "manifest_digest": "ea7523274c061555fc09f22a2a5a05525e8263779dd4affb01af8c98f5856815",
        "release_draft": "false",
        "release_prerelease": "false",
        "release_immutable": "false",
    }
    inputs = dispatch["inputs"]
    assert set(inputs) == set(expected_defaults)
    for name, expected in expected_defaults.items():
        assert inputs[name] == {"required": True, "type": "string", "default": expected}

    assert {"recovery-build", "recovery-publish", "recovery-attach-release-assets"} < set(
        workflow["jobs"]
    )
    build = workflow["jobs"]["recovery-build"]
    assert build["if"] == "github.event_name == 'workflow_dispatch'"
    assert workflow["jobs"]["recovery-publish"]["if"] == "github.event_name == 'workflow_dispatch'"
    assert workflow["jobs"]["recovery-attach-release-assets"]["if"] == (
        "github.event_name == 'workflow_dispatch' && needs.recovery-publish.result == 'success'"
    )
    checkout = build["steps"][0]
    assert checkout["with"] == {
        "ref": "${{ inputs.tag }}",
        "fetch-depth": 0,
        "persist-credentials": False,
    }
    assert build["outputs"] == {
        "source_sha": "${{ steps.source.outputs.source_sha }}",
        "release_id": "${{ steps.source.outputs.release_id }}",
        "release_draft": "${{ steps.source.outputs.release_draft }}",
        "release_prerelease": "${{ steps.source.outputs.release_prerelease }}",
        "release_immutable": "${{ steps.source.outputs.release_immutable }}",
        "artifact_id": "${{ steps.upload.outputs.artifact-id }}",
        "artifact_digest": "${{ steps.upload.outputs.artifact-digest }}",
        "manifest_digest": "${{ steps.hashes.outputs.manifest_digest }}",
    }


def test_recovery_build_fails_closed_on_incident_or_live_identity_drift() -> None:
    workflow = _load(RECOVERY_WORKFLOW)
    build = workflow["jobs"]["recovery-build"]
    source = next(step for step in build["steps"] if step.get("id") == "source")
    run = source["run"]

    guards = [
        ('test "$TAG_NAME" = "v0.1.3"', "TAG_NAME", "v0.1.3", "v0.1.4"),
        (
            'test "$EXPECTED_SOURCE_SHA" = "d921113c14ec1c270897b70d553d1261d7a20fa1"',
            "EXPECTED_SOURCE_SHA",
            "d921113c14ec1c270897b70d553d1261d7a20fa1",
            "a" * 40,
        ),
        ('test "$EXPECTED_RELEASE_ID" = "377552005"', "EXPECTED_RELEASE_ID", "377552005", "1"),
        (
            'test "$ORIGINAL_RUN_ID" = "33037251075"',
            "ORIGINAL_RUN_ID",
            "33037251075",
            "1",
        ),
        (
            'test "$ORIGINAL_ARTIFACT_ID" = "9632474230"',
            "ORIGINAL_ARTIFACT_ID",
            "9632474230",
            "1",
        ),
    ]
    for command, name, valid, drifted in guards:
        assert _run_workflow_guard(run, command, {name: valid}) == 0
        assert _run_workflow_guard(run, command, {name: drifted}) != 0

    required_live_recaptures = [
        'git fetch --force origin "refs/tags/$TAG_NAME:refs/tags/$TAG_NAME"',
        'TAG_JSON=$(gh api "repos/$GITHUB_REPOSITORY/git/ref/tags/$TAG_NAME")',
        'RELEASE_JSON=$(gh api "repos/$GITHUB_REPOSITORY/releases/tags/$TAG_NAME")',
        'RUN_JSON=$(gh api "repos/$GITHUB_REPOSITORY/actions/runs/$ORIGINAL_RUN_ID")',
        'test "$TAG_SHA" = "$EXPECTED_SOURCE_SHA"',
        'test "$RELEASE_TARGET" = "$EXPECTED_SOURCE_SHA"',
        'test "$CURRENT_RELEASE_ID" = "$EXPECTED_RELEASE_ID"',
        'test "$RUN_HEAD_SHA" = "$EXPECTED_SOURCE_SHA"',
        'test "$RUN_CONCLUSION" = "failure"',
    ]
    for line in required_live_recaptures:
        assert line in run

    upload = next(step for step in build["steps"] if step.get("id") == "upload")
    assert upload["with"] == {
        "name": "recovery-python-dist-v0.1.3-${{ github.run_id }}",
        "path": "dist/*.whl\ndist/*.tar.gz\ndist/SHA256SUMS\n",
        "if-no-files-found": "error",
        "compression-level": 0,
        "retention-days": 7,
    }


def test_recovery_pypi_is_bound_to_the_new_artifact_and_idempotent_exact_files() -> None:
    workflow = _load(RECOVERY_WORKFLOW)
    publish = workflow["jobs"]["recovery-publish"]
    assert publish["needs"] == "recovery-build"
    assert publish["environment"] == {
        "name": "pypi",
        "url": "https://pypi.org/p/dcc-mcp-wwise",
    }
    assert publish["permissions"] == {
        "actions": "read",
        "contents": "read",
        "id-token": "write",
    }

    download, identity, publisher = publish["steps"]
    assert download["with"] == {
        "artifact-ids": "${{ needs.recovery-build.outputs.artifact_id }}",
        "path": "release-bundle",
        "merge-multiple": True,
    }
    run = identity["run"]
    for required in (
        'ARTIFACT_SHA256=$(canonical_artifact_sha256 "$ARTIFACT_DIGEST")',
        'CURRENT_ARTIFACT_JSON=$(gh api "repos/$GITHUB_REPOSITORY/actions/artifacts/$ARTIFACT_ID")',
        'CURRENT_ARTIFACT_SHA256=$(canonical_artifact_sha256 "$CURRENT_ARTIFACT_DIGEST")',
        'test "$CURRENT_ARTIFACT_SHA256" = "$ARTIFACT_SHA256"',
        'test "$CURRENT_ARTIFACT_EXPIRED" = "false"',
        'test "$ARTIFACT_RUN_ID" = "$EXPECTED_RUN_ID"',
        'test "$TAG_SHA" = "$VERIFIED_SOURCE_SHA"',
        'test "$CURRENT_RELEASE_ID" = "$VERIFIED_RELEASE_ID"',
        "(cd release-bundle && sha256sum --check SHA256SUMS)",
        "cp release-bundle/*.whl release-bundle/*.tar.gz pypi-dist/",
        "https://pypi.org/pypi/dcc-mcp-wwise/0.1.3/json",
        'test "$(jq \'.urls | length\' "$PYPI_JSON")" -eq 2',
        'echo "publish_required=false" >> "$GITHUB_OUTPUT"',
        'echo "publish_required=true" >> "$GITHUB_OUTPUT"',
    ):
        assert required in run
    assert run.index("CURRENT_ARTIFACT_JSON=$(gh api") < run.index("publish_required=")
    assert publisher["if"] == "steps.identity.outputs.publish_required == 'true'"
    assert publisher["uses"] == (
        "pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33"
    )
    assert publisher["with"] == {
        "packages-dir": "pypi-dist",
        "verbose": True,
        "print-hash": True,
    }


def test_recovery_assets_are_idempotent_and_recap_every_identity_before_post() -> None:
    workflow = _load(RECOVERY_WORKFLOW)
    attach = workflow["jobs"]["recovery-attach-release-assets"]
    assert attach["needs"] == ["recovery-build", "recovery-publish"]
    assert attach["if"] == (
        "github.event_name == 'workflow_dispatch' && needs.recovery-publish.result == 'success'"
    )
    assert attach["permissions"] == {"actions": "read", "contents": "write"}
    download, identity = attach["steps"]
    assert download["with"] == {
        "artifact-ids": "${{ needs.recovery-build.outputs.artifact_id }}",
        "path": "release-assets",
        "merge-multiple": True,
    }
    run = identity["run"]
    upload = (
        'gh api --method POST "https://uploads.github.com/repos/$GITHUB_REPOSITORY/'
        'releases/$CURRENT_RELEASE_ID/assets?name=$ENCODED_NAME"'
    )
    assert run.count(upload) == 1
    loop = run.rsplit("for asset in release-assets/*; do", 1)[-1]
    for required in (
        "ASSET_MATCH_COUNT=$(jq",
        'EXISTING_ASSET_ID=$(jq -r --arg name "$name"',
        'gh api --header "Accept: application/octet-stream"',
        'test "$EXISTING_SHA256" = "$LOCAL_SHA256"',
        'CURRENT_ARTIFACT_JSON=$(gh api "repos/$GITHUB_REPOSITORY/actions/artifacts/$ARTIFACT_ID")',
        'CURRENT_ARTIFACT_SHA256=$(canonical_artifact_sha256 "$CURRENT_ARTIFACT_DIGEST")',
        'test "$CURRENT_ARTIFACT_EXPIRED" = "false"',
        'CURRENT_RELEASE_JSON=$(gh api "repos/$GITHUB_REPOSITORY/releases/tags/$TAG_NAME")',
        'TAG_SHA=$(gh api "repos/$GITHUB_REPOSITORY/git/ref/tags/$TAG_NAME"',
        'test "$TAG_SHA" = "$VERIFIED_SOURCE_SHA"',
        'test "$CURRENT_RELEASE_ID" = "$VERIFIED_RELEASE_ID"',
    ):
        assert required in loop
    assert loop.index("CURRENT_ARTIFACT_JSON=$(gh api") < loop.index(upload)
    assert loop.index("CURRENT_RELEASE_JSON=$(gh api") < loop.index(upload)
    assert loop.index("TAG_SHA=$(gh api") < loop.index(upload)
    assert (
        _run_workflow_guard(
            loop,
            'test "$EXISTING_SHA256" = "$LOCAL_SHA256"',
            {"EXISTING_SHA256": "a" * 64, "LOCAL_SHA256": "a" * 64},
        )
        == 0
    )
    assert (
        _run_workflow_guard(
            loop,
            'test "$EXISTING_SHA256" = "$LOCAL_SHA256"',
            {"EXISTING_SHA256": "a" * 64, "LOCAL_SHA256": "b" * 64},
        )
        != 0
    )


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
    expected_digest = "a" * 64
    mismatched_digest = "b" * 64
    guard = 'test "$CURRENT_ARTIFACT_SHA256" = "$ARTIFACT_SHA256"'
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
                    "ARTIFACT_SHA256": expected_digest,
                    "CURRENT_ARTIFACT_SHA256": mismatched_digest,
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
