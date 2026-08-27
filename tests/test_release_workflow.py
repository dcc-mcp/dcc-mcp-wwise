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


def _lock_sync_document():
    return yaml_loads(
        (ROOT / ".github/workflows/release-please-lock-sync.yml").read_text(encoding="utf-8")
    )


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


def test_release_contract_freezes_release_please_inputs() -> None:
    from scripts.ci.check_workflows import validate_release

    document = _release_document()
    release = document["jobs"]["release-please"]["steps"][0]
    release["with"]["token"] = "${{ secrets.PERSONAL_ACCESS_TOKEN }}"
    release["with"]["repo-url"] = "other/repository"

    with pytest.raises(ValueError, match="release-please.*inputs"):
        validate_release(document)


def test_release_contract_freezes_checkout_inputs_and_every_run_body() -> None:
    from scripts.ci.check_workflows import validate_release

    document = _release_document()
    checkout = document["jobs"]["build"]["steps"][0]
    checkout["with"]["persist-credentials"] = True
    checkout["with"]["token"] = "${{ secrets.PERSONAL_ACCESS_TOKEN }}"
    with pytest.raises(ValueError, match="checkout.*inputs"):
        validate_release(document)

    original = _release_document()
    run_steps = [
        (job_name, index)
        for job_name, job in original["jobs"].items()
        for index, step in enumerate(job["steps"])
        if "run" in step
    ]
    assert run_steps
    for job_name, index in run_steps:
        document = _release_document()
        document["jobs"][job_name]["steps"][index]["run"] += (
            '\ngit push "https://github.com/other/repository.git" HEAD:main\n'
        )
        with pytest.raises(ValueError, match="closed mutation surface"):
            validate_release(document)

    document = _release_document()
    document["jobs"]["build"]["steps"][4]["env"] = {"WRITE_TOKEN": "${{ secrets.WRITE_TOKEN }}"}
    with pytest.raises(ValueError, match="extra credentials"):
        validate_release(document)


def test_release_consumers_are_bound_to_the_build_artifact_id() -> None:
    from scripts.ci.check_workflows import validate_release

    document = _release_document()
    document["jobs"]["publish"]["steps"][0]["with"]["artifact-ids"] = "1"

    with pytest.raises(ValueError, match="exact build artifact ID"):
        validate_release(document)


def test_release_rejects_warning_only_download_hash_verification() -> None:
    from scripts.ci.check_workflows import validate_release

    document = _release_document()
    identity = document["jobs"]["publish"]["steps"][1]
    identity["run"] = identity["run"].replace(
        "(cd release-bundle && sha256sum --check SHA256SUMS)",
        '(cd release-bundle && sha256sum --check SHA256SUMS) || echo "::warning::digest mismatch"',
    )

    with pytest.raises(ValueError, match="closed mutation surface"):
        validate_release(document)


@pytest.mark.parametrize("job_name", ["publish", "attach-release-assets"])
def test_release_rejects_warning_only_server_artifact_digest(job_name: str) -> None:
    from scripts.ci.check_workflows import validate_release

    document = _release_document()
    identity = document["jobs"][job_name]["steps"][1]
    identity["run"] = identity["run"].replace(
        'test "$CURRENT_ARTIFACT_DIGEST" = "$ARTIFACT_DIGEST"',
        'test "$CURRENT_ARTIFACT_DIGEST" = "$ARTIFACT_DIGEST" '
        '|| echo "::warning::server digest mismatch"',
    )

    with pytest.raises(ValueError, match="closed mutation surface"):
        validate_release(document)


def test_release_rejects_missing_per_asset_tag_recapture() -> None:
    from scripts.ci.check_workflows import validate_release

    document = _release_document()
    attach = document["jobs"]["attach-release-assets"]["steps"][1]
    loop_start = attach["run"].rfind("for asset in release-assets/*; do")
    prefix = attach["run"][:loop_start]
    loop = attach["run"][loop_start:]
    loop = loop.replace(
        'TAG_SHA=$(gh api "repos/$GITHUB_REPOSITORY/git/ref/tags/$TAG_NAME" '
        "--jq '.object.sha')\n",
        "",
        1,
    ).replace('test "$TAG_SHA" = "$VERIFIED_SOURCE_SHA"\n', "", 1)
    attach["run"] = prefix + loop

    with pytest.raises(ValueError, match="closed mutation surface"):
        validate_release(document)


def test_release_rejects_untrusted_or_missing_manifest_digest() -> None:
    from scripts.ci.check_workflows import validate_release

    document = _release_document()
    document["jobs"]["build"]["outputs"]["manifest_digest"] = "decoy"
    with pytest.raises(ValueError, match="trusted hash manifest"):
        validate_release(document)

    document = _release_document()
    del document["jobs"]["publish"]["steps"][1]["env"]["MANIFEST_DIGEST"]
    with pytest.raises(ValueError, match="exact identity step binding"):
        validate_release(document)


def test_release_rejects_assets_that_can_run_without_successful_pypi() -> None:
    from scripts.ci.check_workflows import validate_release

    document = _release_document()
    document["jobs"]["attach-release-assets"]["needs"].remove("publish")
    with pytest.raises(ValueError, match="successful PyPI"):
        validate_release(document)

    document = _release_document()
    document["jobs"]["attach-release-assets"]["if"] = (
        "needs.release-please.outputs.release_created == 'true'"
    )
    with pytest.raises(ValueError, match="PyPI publication fails"):
        validate_release(document)


def test_release_rejects_missing_final_release_state_recapture() -> None:
    from scripts.ci.check_workflows import validate_release

    document = _release_document()
    attach = document["jobs"]["attach-release-assets"]["steps"][1]
    marker = 'CURRENT_RELEASE_JSON=$(gh api "repos/$GITHUB_REPOSITORY/releases/tags/$TAG_NAME")'
    first, second = attach["run"].split(marker, 1)
    attach["run"] = first + marker + second.replace(marker, "", 1)

    with pytest.raises(ValueError, match="closed mutation surface"):
        validate_release(document)


def test_lock_sync_rejects_write_credentials_outside_final_push() -> None:
    from scripts.ci.check_workflows import validate_lock_sync

    document = _lock_sync_document()
    generation = document["jobs"]["generate-release-lock"]
    generation["steps"][0]["with"]["token"] = "${{ secrets.PERSONAL_ACCESS_TOKEN }}"

    with pytest.raises(ValueError, match="credential-free"):
        validate_lock_sync(document)

    document = _lock_sync_document()
    document["jobs"]["generate-release-lock"]["env"] = {
        "GH_TOKEN": "${{ secrets.PERSONAL_ACCESS_TOKEN }}"
    }
    with pytest.raises(ValueError, match="credential-free job surface"):
        validate_lock_sync(document)


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


@pytest.mark.parametrize(
    "suffix",
    [
        "; python -m twine upload dist/*",
        " && python -m twine upload dist/*",
        " || python -m twine upload dist/*",
        " | python -m twine upload dist/*",
        " $(python -m twine upload dist/*)",
        " \\\npython -m twine upload dist/*",
        "; $PUBLISHER dist/*",
    ],
)
def test_release_rejects_same_line_suffixes_on_authoritative_gh_api(
    suffix: str,
) -> None:
    from scripts.ci.check_workflows import validate_release

    document = _release_document()
    identity = document["jobs"]["publish"]["steps"][1]
    authoritative = (
        "TAG_SHA=$(gh api \"repos/$GITHUB_REPOSITORY/git/ref/tags/$TAG_NAME\" --jq '.object.sha')"
    )
    identity["run"] = identity["run"].replace(authoritative, authoritative + suffix, 1)

    with pytest.raises(ValueError, match="closed mutation surface"):
        validate_release(document)


def test_release_ignores_comments_but_rejects_echo_decoys_in_identity_step() -> None:
    from scripts.ci.check_workflows import validate_release

    document = _release_document()
    identity = document["jobs"]["publish"]["steps"][1]
    identity["run"] += "\n# inert TAG_SHA=$(gh api decoy)\n"
    validate_release(document)

    identity["run"] += "\necho 'TAG_SHA=$(gh api decoy)'\n"
    with pytest.raises(ValueError, match="closed mutation surface"):
        validate_release(document)


def test_lock_sync_rejects_broad_or_additional_staging() -> None:
    from scripts.ci.check_workflows import validate_lock_sync

    for replacement in ("git add .", "git add -A", "git add uv.lock README.md"):
        document = _lock_sync_document()
        commit = document["jobs"]["sync-release-lock"]["steps"][2]
        commit["run"] = commit["run"].replace("git add uv.lock", replacement)

        with pytest.raises(ValueError, match="closed mutation surface"):
            validate_lock_sync(document)


def test_lock_sync_rejects_additional_push_or_mutation_step() -> None:
    from scripts.ci.check_workflows import validate_lock_sync

    document = _lock_sync_document()
    commit = document["jobs"]["sync-release-lock"]["steps"][-1]
    commit["run"] += '\ngit push origin "HEAD:decoy"\n'
    with pytest.raises(ValueError, match="closed mutation surface"):
        validate_lock_sync(document)

    document = _lock_sync_document()
    document["jobs"]["sync-release-lock"]["steps"].append(
        {"name": "Decoy mutation", "run": "git add ."}
    )
    with pytest.raises(ValueError, match="reviewed ordered steps"):
        validate_lock_sync(document)


@pytest.mark.parametrize(
    "decoy",
    [
        "# git add .",
        'git commit --allow-empty -m "decoy"',
        'gh api --method PATCH "repos/$GITHUB_REPOSITORY"',
    ],
)
def test_lock_sync_rejects_comment_decoys_and_extra_write_commands(
    decoy: str,
) -> None:
    from scripts.ci.check_workflows import validate_lock_sync

    document = _lock_sync_document()
    commit = document["jobs"]["sync-release-lock"]["steps"][-1]
    commit["run"] += "\n{}\n".format(decoy)

    with pytest.raises(ValueError, match="closed mutation surface"):
        validate_lock_sync(document)


def test_lock_sync_rejects_duplicate_or_reordered_steps() -> None:
    from scripts.ci.check_workflows import validate_lock_sync

    document = _lock_sync_document()
    steps = document["jobs"]["sync-release-lock"]["steps"]
    steps.append(dict(steps[-1]))
    with pytest.raises(ValueError, match="reviewed ordered steps"):
        validate_lock_sync(document)

    document = _lock_sync_document()
    steps = document["jobs"]["sync-release-lock"]["steps"]
    steps[2], steps[3] = steps[3], steps[2]
    with pytest.raises(ValueError, match="reviewed ordered steps"):
        validate_lock_sync(document)


@pytest.mark.parametrize(
    ("job", "step_index", "needle"),
    [
        (
            "verify-release-source",
            1,
            'TAG_SHA=$(gh api "repos/$GITHUB_REPOSITORY/git/ref/tags/$TAG_NAME" '
            "--jq '.object.sha')",
        ),
        (
            "build",
            2,
            'TAG_SHA=$(git rev-parse "refs/tags/$TAG_NAME^{commit}")',
        ),
        (
            "attach-release-assets",
            1,
            "CURRENT_RELEASE_ID=$(jq -r '.id' <<< \"$CURRENT_RELEASE_JSON\")",
        ),
    ],
)
def test_every_sensitive_release_run_rejects_prefixed_commands(
    job: str, step_index: int, needle: str
) -> None:
    from scripts.ci.check_workflows import validate_release

    document = _release_document()
    step = document["jobs"][job]["steps"][step_index]
    step["run"] = step["run"].replace(needle, "true && " + needle, 1)

    with pytest.raises(ValueError, match="closed mutation surface"):
        validate_release(document)
