from __future__ import annotations

import base64
import json
import shlex
import subprocess
from glob import glob
from pathlib import Path
from shutil import copy2

import pytest
from dcc_mcp_core import yaml_loads
from twine.exceptions import InvalidDistribution
from twine.package import PackageFile

from scripts.ci.release_integrity import (
    INCIDENT,
    RELEASE,
    server_artifact_sha256,
    upload_artifact_sha256,
)

ROOT = Path(__file__).resolve().parents[1]
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
RECOVERY_WORKFLOW = RELEASE_WORKFLOW
LOCK_SYNC_WORKFLOW = ROOT / ".github" / "workflows" / "release-please-lock-sync.yml"


def _load(path: Path):
    return yaml_loads(path.read_text(encoding="utf-8"))


def _eligible_recovery_jobs(workflow: dict, *, event_name: str, ref: str) -> list[str]:
    context = {
        "github.event_name == 'workflow_dispatch'": event_name == "workflow_dispatch",
        "github.ref == 'refs/heads/main'": ref == "refs/heads/main",
        "needs.recovery-publish.result == 'success'": True,
    }
    eligible: list[str] = []
    for job_name in ("recovery-build", "recovery-publish", "recovery-attach-release-assets"):
        clauses = [clause.strip() for clause in workflow["jobs"][job_name]["if"].split("&&")]
        if not set(clauses) <= set(context):
            raise AssertionError(f"unmodeled recovery condition: {clauses}")
        if all(context[clause] for clause in clauses):
            eligible.append(job_name)
    return eligible


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


def _normal_release_state_recaptures(workflow: dict) -> dict[str, str]:
    publish = next(
        step["run"]
        for step in workflow["jobs"]["publish"]["steps"]
        if step.get("name") == "Verify immutable identity immediately before PyPI"
    )
    attach = next(
        step["run"]
        for step in workflow["jobs"]["attach-release-assets"]["steps"]
        if step.get("name") == "Verify identity and attach assets without clobbering"
    )

    publish_start = publish.index("CURRENT_RELEASE_JSON=$(gh api")
    publish_end = publish.index('test "$(find release-bundle', publish_start)
    attach_preflight_start = attach.index("CURRENT_RELEASE_JSON=$(gh api")
    attach_preflight_end = attach.index('test "$(find release-assets', attach_preflight_start)
    asset_start = attach.index("CURRENT_RELEASE_JSON=$(gh api", attach_preflight_start + 1)
    asset_end = attach.index("TAG_SHA=$(gh api", asset_start)
    return {
        "pypi": publish[publish_start:publish_end],
        "asset_preflight": attach[attach_preflight_start:attach_preflight_end],
        "per_asset": attach[asset_start:asset_end],
    }


def _run_release_state_recapture(block: str, payload: dict[str, object]) -> int:
    encoded_payload = base64.b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    lines = block.splitlines()
    assignment = next(
        index for index, line in enumerate(lines) if "CURRENT_RELEASE_JSON=$(gh api" in line
    )
    indentation = lines[assignment][: len(lines[assignment]) - len(lines[assignment].lstrip())]
    lines[assignment] = (
        f'{indentation}CURRENT_RELEASE_JSON=$(printf %s "{encoded_payload}" | base64 -d)'
    )
    block = "\n".join(lines).replace("release.json", "release-state-recapture-test.json")
    jq_python = (
        "import json,sys;"
        "data=json.load(sys.stdin);"
        "q=sys.argv[1];"
        "key=q.split()[0][1:];"
        "value=data.get(key);"
        'value=False if "// false" in q and value is None else value;'
        'print(value if isinstance(value,str) else json.dumps(value,separators=(",",":")))'
    )
    jq = f"""
jq() {{
  local query="${{@: -1}}"
  python3 -c {shlex.quote(jq_python)} "$query"
}}
"""
    script = f"""
set -euo pipefail
trap 'rm -f release-state-recapture-test.json' EXIT
python() {{ python3 "$@"; }}
{jq}
VERIFIED_SOURCE_SHA=e31a6b9430f1b9f9494401c66d52e87ecb31fca4
TAG_SHA=e31a6b9430f1b9f9494401c66d52e87ecb31fca4
VERIFIED_RELEASE_ID=378005400
VERIFIED_RELEASE_NODE_ID=RE_kwDOTnYlVs4Wh-eY
VERIFIED_RELEASE_NAME=v0.1.4
VERIFIED_RELEASE_DRAFT=false
VERIFIED_RELEASE_PRERELEASE=false
VERIFIED_RELEASE_IMMUTABLE=false
TAG_NAME=v0.1.4
{block}
"""
    encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")
    completed = subprocess.run(
        ["bash", "-c", f"printf %s {encoded} | base64 -d | bash"],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    return completed.returncode


def _run_digest_parser(
    run: str, function: str, value: str, expected: str
) -> subprocess.CompletedProcess[bytes]:
    function_start = run.index(f"{function}() {{")
    function_end = run.index("\n}\n", function_start) + 3
    assertion = 'normalized=$({} {}); test "$normalized" = {}\n'.format(
        function, shlex.quote(value), shlex.quote(expected)
    )
    script = run[function_start:function_end] + "\n" + assertion
    script = script.replace("\r", "")
    encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")
    return subprocess.run(
        ["bash", "-c", f"printf %s {encoded} | base64 -d | bash"],
        check=False,
        capture_output=True,
    )


def test_python_artifact_digest_parsers_keep_upload_and_server_grammars_separate() -> None:
    digest = "a" * 64
    assert upload_artifact_sha256(digest) == digest
    assert server_artifact_sha256(f"sha256:{digest}") == digest


def test_publisher_shell_parsers_accept_only_the_upload_action_bare_digest() -> None:
    digest = "a" * 64
    workflow = _load(RELEASE_WORKFLOW)
    steps = (
        ("publish", "Verify immutable identity immediately before PyPI"),
        ("attach-release-assets", "Verify identity and attach assets without clobbering"),
    )

    for job_name, step_name in steps:
        run = next(
            step["run"]
            for step in workflow["jobs"][job_name]["steps"]
            if step.get("name") == step_name
        )
        completed = _run_digest_parser(run, "upload_artifact_sha256", digest, digest)
        assert completed.returncode == 0, completed.stderr.decode(errors="replace")


@pytest.mark.parametrize(
    "value",
    [
        "SHA256:" + "a" * 64,
        "sha256:" + "A" * 64,
        "sha256:" + "a" * 64 + " ",
        "sha256:" + "a" * 64 + ":decoy",
    ],
)
def test_publisher_shell_parsers_reject_noncanonical_upload_digests(value: str) -> None:
    workflow = _load(RELEASE_WORKFLOW)
    run = next(
        step["run"]
        for step in workflow["jobs"]["publish"]["steps"]
        if step.get("name") == "Verify immutable identity immediately before PyPI"
    )

    assert _run_digest_parser(run, "upload_artifact_sha256", value, "a" * 64).returncode != 0


@pytest.mark.parametrize(
    "value",
    [
        "",
        "a" * 63,
        "a" * 65,
        "sha256:" + "a" * 64,
        "sha512:" + "a" * 64,
        "sha256:" + "A" * 64,
        "sha256:" + "a" * 64 + " ",
        " sha256:" + "a" * 64,
        "sha256:" + "g" * 64,
    ],
)
def test_publishers_reject_noncanonical_sha256_artifact_identities(value: str) -> None:
    with pytest.raises(ValueError):
        upload_artifact_sha256(value)


def test_release_source_accepts_github_base64url_node_ids_and_rejects_malformed_values() -> None:
    workflow = _load(RELEASE_WORKFLOW)
    run = next(
        step["run"]
        for step in workflow["jobs"]["verify-release-source"]["steps"]
        if step.get("name") == "Bind tag and GitHub Release to the checked-out source"
    )

    assert "python scripts/ci/release_integrity.py release release.json" in run
    assert '--github-output "$GITHUB_OUTPUT"' in run
    assert "RELEASE_NODE_ID=$(jq" not in run


def test_normal_release_preflight_uses_the_shared_strict_state_validator() -> None:
    workflow = _load(RELEASE_WORKFLOW)
    run = next(
        step["run"]
        for step in workflow["jobs"]["verify-release-source"]["steps"]
        if step.get("name") == "Bind tag and GitHub Release to the checked-out source"
    )

    assert 'releases/tags/$TAG_NAME" > release.json' in run
    assert "python scripts/ci/release_integrity.py release release.json" in run
    assert ".immutable // false" not in run


@pytest.mark.parametrize("surface", ["pypi", "asset_preflight", "per_asset"])
def test_normal_release_recaptures_accept_exact_typed_release_state(surface: str) -> None:
    blocks = _normal_release_state_recaptures(_load(RELEASE_WORKFLOW))
    payload: dict[str, object] = {
        "id": 378005400,
        "node_id": "RE_kwDOTnYlVs4Wh-eY",
        "name": "v0.1.4",
        "tag_name": "v0.1.4",
        "target_commitish": "e31a6b9430f1b9f9494401c66d52e87ecb31fca4",
        "draft": False,
        "prerelease": False,
        "immutable": False,
    }

    assert _run_release_state_recapture(blocks[surface], payload) == 0


@pytest.mark.parametrize("surface", ["pypi", "asset_preflight", "per_asset"])
@pytest.mark.parametrize("field", ["draft", "prerelease", "immutable"])
@pytest.mark.parametrize("invalid", ["missing", None, "false", 0])
def test_normal_release_recaptures_reject_untyped_or_missing_state_drift(
    surface: str, field: str, invalid: object
) -> None:
    blocks = _normal_release_state_recaptures(_load(RELEASE_WORKFLOW))
    payload: dict[str, object] = {
        "id": 378005400,
        "node_id": "RE_kwDOTnYlVs4Wh-eY",
        "name": "v0.1.4",
        "tag_name": "v0.1.4",
        "target_commitish": "e31a6b9430f1b9f9494401c66d52e87ecb31fca4",
        "draft": False,
        "prerelease": False,
        "immutable": False,
    }
    if invalid == "missing":
        del payload[field]
    else:
        payload[field] = invalid

    assert _run_release_state_recapture(blocks[surface], payload) != 0


@pytest.mark.parametrize("surface", ["pypi", "asset_preflight", "per_asset"])
@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("id", "378005400"),
        ("node_id", "RE_kwDOTnYlVs4Wh-eY-decoy"),
        ("name", "decoy-v0.1.4"),
        ("tag_name", "v0.1.4-decoy"),
        ("target_commitish", "0" * 40),
    ],
)
def test_normal_release_irreversible_surfaces_reject_full_identity_drift(
    surface: str, field: str, invalid: object
) -> None:
    blocks = _normal_release_state_recaptures(_load(RELEASE_WORKFLOW))
    payload: dict[str, object] = {
        "id": 378005400,
        "node_id": "RE_kwDOTnYlVs4Wh-eY",
        "name": "v0.1.4",
        "tag_name": "v0.1.4",
        "target_commitish": "e31a6b9430f1b9f9494401c66d52e87ecb31fca4",
        "draft": False,
        "prerelease": False,
        "immutable": False,
    }
    payload[field] = invalid

    assert _run_release_state_recapture(blocks[surface], payload) != 0


def test_recovery_dispatch_is_frozen_to_the_v014_incident_and_rebuilds_the_tag() -> None:
    workflow = _load(RECOVERY_WORKFLOW)
    dispatch = workflow["on"]["workflow_dispatch"]
    expected_defaults = {
        "tag": "v0.1.4",
        "source_sha": "e31a6b9430f1b9f9494401c66d52e87ecb31fca4",
        "release_id": "378005400",
        "release_node_id": "RE_kwDOTnYlVs4Wh-eY",
        "original_run_id": "33098798286",
        "release_draft": "false",
        "release_prerelease": "false",
        "release_immutable": "false",
    }
    inputs = dispatch["inputs"]
    assert set(inputs) == set(expected_defaults)
    for name, expected in expected_defaults.items():
        assert inputs[name] == {"required": True, "type": "string", "default": expected}
    assert RELEASE.release_id == int(expected_defaults["release_id"])
    assert RELEASE.node_id == expected_defaults["release_node_id"]
    assert RELEASE.tag == expected_defaults["tag"]
    assert RELEASE.target == expected_defaults["source_sha"]
    assert INCIDENT.run_id == int(expected_defaults["original_run_id"])
    assert INCIDENT.head_sha == expected_defaults["source_sha"]

    assert {"recovery-build", "recovery-publish", "recovery-attach-release-assets"} < set(
        workflow["jobs"]
    )
    build = workflow["jobs"]["recovery-build"]
    dispatch_main = "github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main'"
    assert build["if"] == dispatch_main
    assert workflow["jobs"]["recovery-publish"]["if"] == dispatch_main
    assert workflow["jobs"]["recovery-attach-release-assets"]["if"] == (
        "github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main' && "
        "needs.recovery-publish.result == 'success'"
    )
    checkout = next(
        step
        for step in build["steps"]
        if step.get("name") == "Checkout the immutable publication source separately"
    )
    assert checkout["with"] == {
        "ref": "${{ inputs.tag }}",
        "fetch-depth": 0,
        "persist-credentials": False,
        "path": "tag-source",
    }
    assert build["outputs"] == {
        "source_sha": "${{ steps.source.outputs.source_sha }}",
        "artifact_id": "${{ steps.upload.outputs.artifact-id }}",
        "artifact_digest": "${{ steps.upload.outputs.artifact-digest }}",
        "manifest_digest": "${{ steps.select.outputs.manifest_digest }}",
    }


def test_non_main_recovery_dispatch_has_zero_executable_or_oidc_jobs() -> None:
    workflow = _load(RECOVERY_WORKFLOW)
    branch_jobs = _eligible_recovery_jobs(
        workflow,
        event_name="workflow_dispatch",
        ref="refs/heads/unreviewed-recovery",
    )
    assert branch_jobs == []
    assert not any(
        workflow["jobs"][job_name].get("permissions", {}).get("id-token") == "write"
        for job_name in branch_jobs
    )

    assert _eligible_recovery_jobs(
        workflow,
        event_name="workflow_dispatch",
        ref="refs/heads/main",
    ) == ["recovery-build", "recovery-publish", "recovery-attach-release-assets"]
    assert (
        _eligible_recovery_jobs(
            workflow,
            event_name="push",
            ref="refs/heads/main",
        )
        == []
    )

    branch_surfaces = [
        step
        for job_name in branch_jobs
        for step in workflow["jobs"][job_name]["steps"]
        if "pypi-publish" in str(step.get("uses", ""))
        or "upload-artifact" in str(step.get("uses", ""))
        or "uploads.github.com" in str(step.get("run", ""))
    ]
    assert branch_surfaces == []


@pytest.mark.parametrize(
    "job_name",
    ["recovery-build", "recovery-publish", "recovery-attach-release-assets"],
)
def test_workflow_contract_rejects_recovery_job_without_main_ref_gate(job_name: str) -> None:
    from scripts.ci.check_workflows import validate_recovery

    workflow = _load(RECOVERY_WORKFLOW)
    condition = workflow["jobs"][job_name]["if"]
    condition = condition.replace(" && github.ref == 'refs/heads/main'", "")
    condition = condition.replace("github.ref == 'refs/heads/main' && ", "")
    workflow["jobs"][job_name]["if"] = condition
    with pytest.raises(ValueError):
        validate_recovery(workflow)


def test_recovery_build_fails_closed_on_incident_or_live_identity_drift() -> None:
    workflow = _load(RECOVERY_WORKFLOW)
    build = workflow["jobs"]["recovery-build"]
    source = next(step for step in build["steps"] if step.get("id") == "source")
    run = source["run"]

    guards = [
        ('test "$TAG_NAME" = "v0.1.4"', "TAG_NAME", "v0.1.4", "v0.1.5"),
        (
            'test "$SOURCE_SHA" = "e31a6b9430f1b9f9494401c66d52e87ecb31fca4"',
            "SOURCE_SHA",
            "e31a6b9430f1b9f9494401c66d52e87ecb31fca4",
            "a" * 40,
        ),
        ('test "$RELEASE_ID" = "378005400"', "RELEASE_ID", "378005400", "1"),
        (
            'test "$ORIGINAL_RUN_ID" = "33098798286"',
            "ORIGINAL_RUN_ID",
            "33098798286",
            "1",
        ),
    ]
    for command, name, valid, drifted in guards:
        assert _run_workflow_guard(run, command, {name: valid}) == 0
        assert _run_workflow_guard(run, command, {name: drifted}) != 0

    required_live_recaptures = [
        'gh api "repos/$GITHUB_REPOSITORY/actions/runs/$ORIGINAL_RUN_ID" > incident.json',
        "python scripts/ci/release_integrity.py incident incident.json",
        "python scripts/ci/release_integrity.py release release.json",
    ]
    for line in required_live_recaptures:
        assert line in run

    assert not any(
        step.get("name", "").startswith("Download the exact original") for step in build["steps"]
    )
    upload = next(step for step in build["steps"] if step.get("id") == "upload")
    upload_index = build["steps"].index(upload)
    pre_upload = build["steps"][upload_index - 1]
    assert pre_upload["name"] == "Recapture frozen identity before recovery artifact upload"
    for required in (
        "git/ref/tags/$TAG_NAME",
        "releases/$RELEASE_ID",
        "actions/runs/$ORIGINAL_RUN_ID",
        "release_integrity.py release",
        "release_integrity.py incident",
    ):
        assert required in pre_upload["run"]
    assert upload["with"] == {
        "name": "recovery-python-dist-v0.1.4-${{ github.run_id }}",
        "path": "selected-dist/*.whl\nselected-dist/*.tar.gz\nselected-dist/SHA256SUMS\n",
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

    preflight = next(
        step for step in publish["steps"] if step.get("name", "").startswith("Recapture")
    )
    download = next(
        step for step in publish["steps"] if step.get("name", "").startswith("Download")
    )
    identity = next(step for step in publish["steps"] if step.get("id") == "identity")
    publisher = publish["steps"][-1]
    assert download["with"] == {
        "artifact-ids": "${{ needs.recovery-build.outputs.artifact_id }}",
        "path": "release-bundle",
        "merge-multiple": True,
    }
    run = identity["run"]
    for required in (
        "python scripts/ci/release_integrity.py incident",
        "python scripts/ci/release_integrity.py release",
        "python scripts/ci/release_integrity.py artifact selected-artifact.json",
        "(cd release-bundle && sha256sum --check SHA256SUMS)",
        "python tools/verify_release_archives.py release-bundle/*.whl",
        "--snapshot-dir pypi-dist",
        "https://pypi.org/pypi/dcc-mcp-wwise/0.1.4/json",
        'python scripts/ci/release_integrity.py pypi "$PYPI_JSON" pypi-dist',
        'echo "publish_required=false" >> "$GITHUB_OUTPUT"',
        'echo "publish_required=true" >> "$GITHUB_OUTPUT"',
    ):
        assert required in run
    assert "actions/runs/${{ inputs.original_run_id }}" in preflight["run"]
    assert "actions/artifacts/$ARTIFACT_ID" in preflight["run"]
    assert run.index("selected-artifact.json") < run.index("publish_required=")
    assert publisher["if"] == "steps.identity.outputs.publish_required == 'true'"
    assert publisher["uses"] == (
        "pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33"
    )
    assert publisher["with"] == {
        "packages-dir": "pypi-dist",
        "verbose": True,
        "print-hash": True,
    }


def test_recovery_artifact_provenance_binds_the_recovery_run_head() -> None:
    """The selected artifact belongs to this recovery run, not the v0.1.4 incident."""
    workflow = _load(RECOVERY_WORKFLOW)
    for job_name in ("recovery-publish", "recovery-attach-release-assets"):
        runs = "\n".join(str(step.get("run", "")) for step in workflow["jobs"][job_name]["steps"])
        selected_checks = [
            line
            for line in runs.splitlines()
            if "release_integrity.py artifact selected-artifact.json" in line
        ]
        assert selected_checks
        assert all('--head-sha "$GITHUB_SHA"' in line for line in selected_checks)
        assert all('--head-sha "$SOURCE_SHA"' not in line for line in selected_checks)


def test_recovery_assets_are_idempotent_and_recap_every_identity_before_post() -> None:
    workflow = _load(RECOVERY_WORKFLOW)
    attach = workflow["jobs"]["recovery-attach-release-assets"]
    assert attach["needs"] == ["recovery-build", "recovery-publish"]
    assert attach["if"] == (
        "github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main' && "
        "needs.recovery-publish.result == 'success'"
    )
    assert attach["permissions"] == {"actions": "read", "contents": "write"}
    preflight = next(
        step for step in attach["steps"] if step.get("name", "").startswith("Recapture")
    )
    download = next(step for step in attach["steps"] if step.get("name", "").startswith("Download"))
    identity = attach["steps"][-1]
    assert download["with"] == {
        "artifact-ids": "${{ needs.recovery-build.outputs.artifact_id }}",
        "path": "release-assets",
        "merge-multiple": True,
    }
    run = identity["run"]
    upload = (
        'gh api --method POST "https://uploads.github.com/repos/$GITHUB_REPOSITORY/'
        'releases/${{ inputs.release_id }}/assets?name=$ENCODED_NAME"'
    )
    assert run.count(upload) == 1
    loop = run.rsplit("for asset in verified-assets/*; do", 1)[-1]
    for required in (
        "ASSET_MATCH_COUNT=$(jq",
        'EXISTING_ASSET_ID=$(jq -r --arg name "$name"',
        'gh api --header "Accept: application/octet-stream"',
        'test "$(sha256sum existing-asset | awk \'{print $1}\')" = "$LOCAL_SHA256"',
        'gh api "repos/$GITHUB_REPOSITORY/actions/runs/${{ inputs.original_run_id }}"',
        "python scripts/ci/release_integrity.py incident",
        "python scripts/ci/release_integrity.py release",
        "python scripts/ci/release_integrity.py artifact selected-artifact.json",
        'test "$(gh api "repos/$GITHUB_REPOSITORY/git/ref/tags/$TAG_NAME"',
    ):
        assert required in loop
    assert loop.index("incident.json") < loop.index(upload)
    assert loop.index("selected-artifact.json") < loop.index(upload)
    assert "actions/runs/${{ inputs.original_run_id }}" in preflight["run"]
    assert "actions/artifacts/$ARTIFACT_ID" in preflight["run"]


def test_asset_publication_stops_on_a_mid_loop_tag_move() -> None:
    workflow = _load(RELEASE_WORKFLOW)
    attach_run = next(
        step["run"]
        for step in workflow["jobs"]["attach-release-assets"]["steps"]
        if step.get("name") == "Verify identity and attach assets without clobbering"
    )
    asset_loop = attach_run.rsplit("for asset in verified-assets/*; do", 1)[-1]
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
    for job_name in ("publish", "attach-release-assets"):
        runs = "\n".join(step.get("run", "") for step in workflow["jobs"][job_name]["steps"])
        assert runs.count("release_integrity.py artifact artifact.json") >= 2


def test_pypi_action_glob_contains_only_distributions(tmp_path: Path) -> None:
    workflow = _load(RELEASE_WORKFLOW)
    publish = workflow["jobs"]["publish"]
    download = next(
        step for step in publish["steps"] if step.get("name", "").startswith("Download")
    )
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

    identity_run = next(
        step["run"]
        for step in publish["steps"]
        if step.get("name") == "Verify immutable identity immediately before PyPI"
    )
    assert "--snapshot-dir pypi-dist" in identity_run
    packages.mkdir()
    for pattern in ("*.whl", "*.tar.gz"):
        for distribution in bundle.glob(pattern):
            copy2(distribution, packages / distribution.name)
    action_equivalent_glob = glob(str(packages / "*"))
    assert action_equivalent_glob
    assert all(path.endswith((".whl", ".tar.gz")) for path in action_equivalent_glob)
    assert download["with"]["path"] != publication["with"]["packages-dir"]
    assert "verified-dist/SHA256SUMS" in workflow["jobs"]["build"]["steps"][-2]["with"]["path"]


def test_every_publication_consumes_only_a_verified_private_snapshot() -> None:
    workflow = _load(RELEASE_WORKFLOW)
    build_runs = "\n".join(str(step.get("run", "")) for step in workflow["jobs"]["build"]["steps"])
    recovery_build_runs = "\n".join(
        str(step.get("run", "")) for step in workflow["jobs"]["recovery-build"]["steps"]
    )
    assert "--snapshot-dir verified-dist" in build_runs
    assert "cp dist/" not in build_runs
    assert "--snapshot-dir selected-dist" in recovery_build_runs
    assert "cp dist/" not in recovery_build_runs

    expected = {
        "publish": ("pypi-dist", "release-bundle/SHA256SUMS"),
        "recovery-publish": ("pypi-dist", "release-bundle/SHA256SUMS"),
        "attach-release-assets": ("verified-assets", "verified-assets/SHA256SUMS"),
        "recovery-attach-release-assets": (
            "verified-assets",
            "verified-assets/SHA256SUMS",
        ),
    }
    for job_name, (snapshot, manifest) in expected.items():
        runs = "\n".join(str(step.get("run", "")) for step in workflow["jobs"][job_name]["steps"])
        assert f"--snapshot-dir {snapshot}" in runs
        assert f"cp release-bundle/*.whl release-bundle/*.tar.gz {snapshot}/" not in runs
        assert manifest in runs


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
        "EXPECTED_HEAD_SHA": "${{ needs.generate-release-lock.outputs.source_sha }}",
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
        "release_node_id",
        "release_name",
        "release_draft",
        "release_prerelease",
        "release_immutable",
    }
    for job, expected_recaptures in ((publish, 1), (attach, 2)):
        identity = next(
            step for step in job["steps"] if "VERIFIED_RELEASE_ID" in step.get("env", {})
        )
        assert identity["env"]["VERIFIED_RELEASE_ID"] == (
            "${{ needs.verify-release-source.outputs.release_id }}"
        )
        assert "CURRENT_RELEASE_JSON=$(gh api" in identity["run"]
        assert "CURRENT_RELEASE_ID=$(jq" not in identity["run"]
        assert identity["run"].count('printf "%s" "$CURRENT_RELEASE_JSON" > release.json') == (
            expected_recaptures
        )
        assert (
            identity["run"].count(
                "python scripts/ci/release_integrity.py release-state release.json"
            )
            == 0
        )
        assert (
            identity["run"].count("python scripts/ci/release_integrity.py release release.json")
            == expected_recaptures
        )
        assert "CURRENT_RELEASE_DRAFT=$(jq" not in identity["run"]
        assert "CURRENT_RELEASE_PRERELEASE=$(jq" not in identity["run"]
        assert "CURRENT_RELEASE_IMMUTABLE=$(jq" not in identity["run"]
        assert ".immutable // false" not in identity["run"]
        assert "VERIFIED_RELEASE_NODE_ID" in identity["env"]
        assert "VERIFIED_RELEASE_NAME" in identity["env"]

    attach_run = next(
        step["run"]
        for step in attach["steps"]
        if step.get("name") == "Verify identity and attach assets without clobbering"
    )
    assert "gh release upload" not in attach_run
    upload = attach_run.index("releases/$CURRENT_RELEASE_ID/assets?name=")
    assert '--input "$asset"' in attach_run[upload:]
    assert attach_run.rfind("CURRENT_RELEASE_JSON=$(gh api", 0, upload) > attach_run.index("done")
