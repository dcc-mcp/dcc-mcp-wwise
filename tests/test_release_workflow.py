from __future__ import annotations

import hashlib
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


def _recovery_document():
    return _release_document()


def _named_step(document, job: str, name: str):
    return next(step for step in document["jobs"][job]["steps"] if step.get("name") == name)


def _git(*arguments: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _model_lock_artifact_sync(tmp_path: Path, artifact_files: dict[str, bytes]) -> bytes:
    """Model download-artifact extraction through the reviewed git push boundary."""
    document = _lock_sync_document()
    download = _named_step(document, "sync-release-lock", "Download exact generated lock")
    checkout = tmp_path / "checkout"
    remote = tmp_path / "remote.git"
    checkout.mkdir()
    _git("init", "--initial-branch=main", cwd=checkout)
    _git("config", "core.autocrlf", "false", cwd=checkout)
    _git("config", "user.name", "loonghao", cwd=checkout)
    _git("config", "user.email", "hal.long@outlook.com", cwd=checkout)
    (checkout / "uv.lock").write_bytes(b'version = 1\nname = "stale-0.1.3"\n')
    _git("add", "uv.lock", cwd=checkout)
    _git("commit", "-m", "initial lock", cwd=checkout)
    branch = "release-please--branches--main--components--dcc-mcp-wwise"
    _git("switch", "-c", branch, cwd=checkout)
    source_sha = _git("rev-parse", "HEAD", cwd=checkout)
    _git("init", "--bare", str(remote), cwd=tmp_path)
    _git("remote", "add", "origin", str(remote), cwd=checkout)
    _git("push", "-u", "origin", branch, cwd=checkout)

    extraction_root = checkout / download["with"]["path"]
    if not download["with"].get("merge-multiple", False):
        extraction_root /= "release-lock-12-33060625683"
    for relative, content in artifact_files.items():
        target = extraction_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    generated_locks = [
        content for relative, content in artifact_files.items() if Path(relative).name == "uv.lock"
    ]
    if len(generated_locks) != 1:
        raise ValueError("artifact must contain exactly one generated uv.lock")
    generated = generated_locks[0]
    expected_digest = hashlib.sha256(generated).hexdigest()
    if hashlib.sha256((checkout / "uv.lock").read_bytes()).hexdigest() != expected_digest:
        raise ValueError("root uv.lock is not the downloaded generated lock")
    if _git("diff", "--name-only", cwd=checkout) != "uv.lock":
        raise ValueError("generated lock must be the only tracked diff")
    if _git("ls-files", "--others", "--exclude-standard", cwd=checkout):
        raise ValueError("downloaded artifact produced an extra or decoy path")
    _git("add", "uv.lock", cwd=checkout)
    if _git("show", ":uv.lock", cwd=checkout).encode() + b"\n" != generated:
        raise ValueError("staged lock is not the generated lock")
    _git("commit", "-m", "chore(ci): sync generated release lock", cwd=checkout)
    if _git("rev-parse", "HEAD^", cwd=checkout) != source_sha:
        raise ValueError("lock commit lost the exact source head lease")
    if _git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD", cwd=checkout) != "uv.lock":
        raise ValueError("lock commit contains an extra path")
    _git("push", "origin", f"HEAD:refs/heads/{branch}", cwd=checkout)
    return (
        _git(
            "--git-dir", str(remote), "show", f"refs/heads/{branch}:uv.lock", cwd=tmp_path
        ).encode()
        + b"\n"
    )


def test_downloaded_generated_lock_is_the_exact_root_file_verified_staged_and_pushed(
    tmp_path: Path,
) -> None:
    generated = b'version = 1\nname = "generated-0.1.4"\n'

    assert _model_lock_artifact_sync(tmp_path, {"uv.lock": generated}) == generated


def test_downloaded_generated_lock_rejects_a_nested_only_layout(tmp_path: Path) -> None:
    generated = b'version = 1\nname = "generated-0.1.4"\n'

    with pytest.raises(ValueError, match="root uv.lock"):
        _model_lock_artifact_sync(tmp_path, {"nested/uv.lock": generated})


@pytest.mark.parametrize(
    "decoy",
    [
        "extra.txt",
        "nested/decoy.txt",
        "nested/uv.lock",
    ],
)
def test_downloaded_generated_lock_rejects_every_extra_or_decoy_path(
    tmp_path: Path, decoy: str
) -> None:
    generated = b'version = 1\nname = "generated-0.1.4"\n'

    with pytest.raises(ValueError):
        _model_lock_artifact_sync(tmp_path, {"uv.lock": generated, decoy: b"decoy\n"})


def test_recovery_contract_rejects_any_extra_publication_mutation() -> None:
    from scripts.ci.check_workflows import validate_recovery

    document = _recovery_document()
    validate_recovery(document)
    document["jobs"]["recovery-attach-release-assets"]["steps"][-1]["run"] += (
        '\ngh release upload "$TAG_NAME" release-assets/*\n'
    )

    with pytest.raises(ValueError, match="closed recovery mutation surface"):
        validate_recovery(document)


def test_recovery_source_is_immutably_frozen_after_review() -> None:
    from scripts.ci.check_workflows import validate_recovery_source

    source = (ROOT / ".github/workflows/release.yml").read_bytes()
    validate_recovery_source(source)
    validate_recovery_source(source.replace(b"\n", b"\r\n"))

    with pytest.raises(ValueError, match="exact reviewed source digest"):
        validate_recovery_source(source + b"\n# decoy\n")

    with pytest.raises(ValueError, match="portable line endings"):
        validate_recovery_source(source.replace(b"\n", b"\r"))


def test_release_security_sources_are_checked_out_with_portable_lf() -> None:
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert ".github/workflows/*.yml text eol=lf" in attributes
    assert "scripts/ci/*.py text eol=lf" in attributes
    assert "tools/*.py text eol=lf" in attributes


@pytest.mark.parametrize(
    "relative",
    ["scripts/ci/release_integrity.py", "tools/verify_release_archives.py"],
)
def test_release_helper_source_freeze_accepts_lf_and_crlf_but_rejects_changes(
    relative: str,
) -> None:
    from scripts.ci.check_workflows import validate_frozen_source

    source = (ROOT / relative).read_bytes()
    expected = hashlib.sha256(source).hexdigest()
    validate_frozen_source(source, expected, relative)
    validate_frozen_source(source.replace(b"\n", b"\r\n"), expected, relative)
    with pytest.raises(ValueError, match="exact reviewed source digest"):
        validate_frozen_source(source + b"# semantic change\n", expected, relative)


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
    from scripts.ci.check_workflows import validate_recovery, validate_release

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
    normal_jobs = {
        "release-please",
        "verify-release-source",
        "build",
        "publish",
        "attach-release-assets",
    }
    for job_name, index in run_steps:
        document = _release_document()
        document["jobs"][job_name]["steps"][index]["run"] += (
            '\ngit push "https://github.com/other/repository.git" HEAD:main\n'
        )
        validator = validate_release if job_name in normal_jobs else validate_recovery
        with pytest.raises(ValueError, match="closed (recovery )?mutation surface"):
            validator(document)

    document = _release_document()
    document["jobs"]["build"]["steps"][4]["env"] = {"WRITE_TOKEN": "${{ secrets.WRITE_TOKEN }}"}
    with pytest.raises(ValueError, match="extra credentials"):
        validate_release(document)


@pytest.mark.parametrize(
    "job_name",
    [
        "release-please",
        "verify-release-source",
        "build",
        "publish",
        "attach-release-assets",
    ],
)
def test_release_rejects_unreviewed_runner_drift(job_name: str) -> None:
    from scripts.ci.check_workflows import validate_release

    document = _release_document()
    document["jobs"][job_name]["runs-on"] = ["self-hosted", "release-secrets"]

    with pytest.raises(ValueError, match="exact reviewed job"):
        validate_release(document)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("container", {"image": "untrusted.example/release:latest"}),
        ("services", {"proxy": {"image": "untrusted.example/proxy:latest"}}),
        ("defaults", {"run": {"shell": "bash -e {0}"}}),
        ("x-decoy", {"if": "always()", "runs-on": "self-hosted"}),
    ],
)
def test_release_rejects_unreviewed_job_execution_controls(field: str, value: object) -> None:
    from scripts.ci.check_workflows import validate_release

    document = _release_document()
    document["jobs"]["publish"][field] = value

    with pytest.raises(ValueError, match="exact reviewed job"):
        validate_release(document)


def test_release_rejects_workflow_defaults_that_change_critical_steps() -> None:
    from scripts.ci.check_workflows import validate_release

    document = _release_document()
    document["defaults"] = {"run": {"shell": "bash -e {0}"}}

    with pytest.raises(ValueError, match="exact reviewed workflow"):
        validate_release(document)


@pytest.mark.parametrize(
    ("step_index", "field", "value"),
    [
        (2, "if", "always()"),
        (1, "continue-on-error", True),
        (0, "timeout-minutes", 1),
        (2, "x-decoy", {"continue-on-error": True}),
    ],
)
def test_release_rejects_unreviewed_step_execution_controls(
    step_index: int, field: str, value: object
) -> None:
    from scripts.ci.check_workflows import validate_release

    document = _release_document()
    document["jobs"]["publish"]["steps"][step_index][field] = value

    with pytest.raises(ValueError, match="exact reviewed step"):
        validate_release(document)


@pytest.mark.parametrize(
    ("path_suffix", "extra_inputs"),
    [
        ("\ndist/private/secret.txt", {}),
        ("\n/etc/shadow", {}),
        ("\ndist/**", {}),
        ("", {"include-hidden-files": True}),
    ],
)
def test_release_rejects_broadened_immutable_artifact_inputs(
    path_suffix: str, extra_inputs: dict[str, object]
) -> None:
    from scripts.ci.check_workflows import validate_release

    document = _release_document()
    upload = document["jobs"]["build"]["steps"][8]["with"]
    upload["path"] += path_suffix
    upload.update(extra_inputs)

    with pytest.raises(ValueError, match="exact reviewed inputs"):
        validate_release(document)


def test_release_consumers_are_bound_to_the_build_artifact_id() -> None:
    from scripts.ci.check_workflows import validate_release

    document = _release_document()
    _named_step(document, "publish", "Download immutable Python distributions")["with"][
        "artifact-ids"
    ] = "1"

    with pytest.raises(ValueError, match="exact build artifact ID"):
        validate_release(document)


def test_release_rejects_warning_only_download_hash_verification() -> None:
    from scripts.ci.check_workflows import validate_release

    document = _release_document()
    identity = _named_step(document, "publish", "Verify immutable identity immediately before PyPI")
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
    preflight = _named_step(
        document, job_name, "Recapture exact artifact provenance before download"
    )
    preflight["run"] += '\ntrue || echo "::warning::server digest mismatch"\n'

    with pytest.raises(ValueError, match="closed mutation surface"):
        validate_release(document)


def test_release_rejects_missing_per_asset_tag_recapture() -> None:
    from scripts.ci.check_workflows import validate_release

    document = _release_document()
    attach = _named_step(
        document, "attach-release-assets", "Verify identity and attach assets without clobbering"
    )
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
    del _named_step(document, "publish", "Verify immutable identity immediately before PyPI")[
        "env"
    ]["MANIFEST_DIGEST"]
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
    attach = _named_step(
        document, "attach-release-assets", "Verify identity and attach assets without clobbering"
    )
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
    identity = _named_step(document, "publish", "Verify immutable identity immediately before PyPI")
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
    identity = _named_step(document, "publish", "Verify immutable identity immediately before PyPI")
    authoritative = (
        "TAG_SHA=$(gh api \"repos/$GITHUB_REPOSITORY/git/ref/tags/$TAG_NAME\" --jq '.object.sha')"
    )
    identity["run"] = identity["run"].replace(authoritative, authoritative + suffix, 1)

    with pytest.raises(ValueError, match="closed mutation surface"):
        validate_release(document)


def test_release_rejects_comment_and_echo_decoys_in_identity_step() -> None:
    from scripts.ci.check_workflows import validate_release

    document = _release_document()
    identity = _named_step(document, "publish", "Verify immutable identity immediately before PyPI")
    identity["run"] += "\n# inert TAG_SHA=$(gh api decoy)\n"
    with pytest.raises(ValueError, match="closed mutation surface"):
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
