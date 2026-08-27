"""Validate parsed GitHub Actions release and compatibility contracts."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from dcc_mcp_core import yaml_loads

ROOT = Path(__file__).resolve().parents[2]
PINNED_ACTION = re.compile(r"\A[^@\s]+@[0-9a-f]{40}\Z")
BUILD_ARTIFACT_ID = "${{ needs.build.outputs.artifact_id }}"
BUILD_ARTIFACT_DIGEST = "${{ needs.build.outputs.artifact_digest }}"
BUILD_MANIFEST_DIGEST = "${{ needs.build.outputs.manifest_digest }}"
CHECKOUT = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
SETUP_PYTHON = "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97"
UPLOAD_ARTIFACT = "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"
DOWNLOAD_ARTIFACT = "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093"
PYPI_PUBLISH = "pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33"
RELEASE_SOURCE_LINES = (
    "set -euo pipefail",
    'git fetch --force origin "refs/tags/$TAG_NAME:refs/tags/$TAG_NAME"',
    "SOURCE_SHA=$(git rev-parse HEAD)",
    "TAG_TYPE=$(gh api \"repos/$GITHUB_REPOSITORY/git/ref/tags/$TAG_NAME\" --jq '.object.type')",
    "TAG_SHA=$(gh api \"repos/$GITHUB_REPOSITORY/git/ref/tags/$TAG_NAME\" --jq '.object.sha')",
    'RELEASE_JSON=$(gh api "repos/$GITHUB_REPOSITORY/releases/tags/$TAG_NAME")',
    "RELEASE_ID=$(jq -r '.id' <<< \"$RELEASE_JSON\")",
    "RELEASE_TARGET=$(jq -r '.target_commitish' <<< \"$RELEASE_JSON\")",
    "RELEASE_DRAFT=$(jq -r '.draft' <<< \"$RELEASE_JSON\")",
    "RELEASE_PRERELEASE=$(jq -r '.prerelease' <<< \"$RELEASE_JSON\")",
    "RELEASE_IMMUTABLE=$(jq -r '.immutable // false' <<< \"$RELEASE_JSON\")",
    'test "$TAG_TYPE" = "commit"',
    'test "$TAG_SHA" = "$SOURCE_SHA"',
    'test "$RELEASE_TARGET" = "$SOURCE_SHA"',
    '[[ "$RELEASE_ID" =~ ^[0-9]+$ ]]',
    'test "$RELEASE_DRAFT" = "false"',
    'test "$RELEASE_PRERELEASE" = "false"',
    '[[ "$RELEASE_IMMUTABLE" =~ ^(true|false)$ ]]',
    'echo "source_sha=$SOURCE_SHA" >> "$GITHUB_OUTPUT"',
    'echo "release_id=$RELEASE_ID" >> "$GITHUB_OUTPUT"',
    'echo "release_draft=$RELEASE_DRAFT" >> "$GITHUB_OUTPUT"',
    'echo "release_prerelease=$RELEASE_PRERELEASE" >> "$GITHUB_OUTPUT"',
    'echo "release_immutable=$RELEASE_IMMUTABLE" >> "$GITHUB_OUTPUT"',
)
BUILD_SOURCE_LINES = (
    "set -euo pipefail",
    'git fetch --force origin "refs/tags/$TAG_NAME:refs/tags/$TAG_NAME"',
    "SOURCE_SHA=$(git rev-parse HEAD)",
    'TAG_SHA=$(git rev-parse "refs/tags/$TAG_NAME^{commit}")',
    'test "$SOURCE_SHA" = "$VERIFIED_SOURCE_SHA"',
    'test "$TAG_SHA" = "$VERIFIED_SOURCE_SHA"',
    'echo "source_sha=$SOURCE_SHA" >> "$GITHUB_OUTPUT"',
)
BUILD_HASH_LINES = (
    "set -euo pipefail",
    "(cd dist && sha256sum --binary *.whl *.tar.gz > SHA256SUMS)",
    "MANIFEST_DIGEST=$(sha256sum dist/SHA256SUMS | awk '{print $1}')",
    '[[ "$MANIFEST_DIGEST" =~ ^[0-9a-f]{64}$ ]]',
    'echo "manifest_digest=$MANIFEST_DIGEST" >> "$GITHUB_OUTPUT"',
)
PYPI_IDENTITY_LINES = (
    "set -euo pipefail",
    'test "$BUILD_SOURCE_SHA" = "$VERIFIED_SOURCE_SHA"',
    'test -n "$ARTIFACT_ID"',
    '[[ "$ARTIFACT_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]',
    '[[ "$MANIFEST_DIGEST" =~ ^[0-9a-f]{64}$ ]]',
    'test "$(sha256sum dist/SHA256SUMS | awk \'{print $1}\')" = "$MANIFEST_DIGEST"',
    "(cd dist && sha256sum --check SHA256SUMS)",
    "TAG_SHA=$(gh api \"repos/$GITHUB_REPOSITORY/git/ref/tags/$TAG_NAME\" --jq '.object.sha')",
    'CURRENT_RELEASE_JSON=$(gh api "repos/$GITHUB_REPOSITORY/releases/tags/$TAG_NAME")',
    "CURRENT_RELEASE_ID=$(jq -r '.id' <<< \"$CURRENT_RELEASE_JSON\")",
    "RELEASE_TARGET=$(jq -r '.target_commitish' <<< \"$CURRENT_RELEASE_JSON\")",
    "CURRENT_RELEASE_DRAFT=$(jq -r '.draft' <<< \"$CURRENT_RELEASE_JSON\")",
    "CURRENT_RELEASE_PRERELEASE=$(jq -r '.prerelease' <<< \"$CURRENT_RELEASE_JSON\")",
    "CURRENT_RELEASE_IMMUTABLE=$(jq -r '.immutable // false' <<< \"$CURRENT_RELEASE_JSON\")",
    'test "$TAG_SHA" = "$VERIFIED_SOURCE_SHA"',
    'test "$RELEASE_TARGET" = "$VERIFIED_SOURCE_SHA"',
    'test "$CURRENT_RELEASE_ID" = "$VERIFIED_RELEASE_ID"',
    'test "$CURRENT_RELEASE_DRAFT" = "$VERIFIED_RELEASE_DRAFT"',
    'test "$CURRENT_RELEASE_PRERELEASE" = "$VERIFIED_RELEASE_PRERELEASE"',
    'test "$CURRENT_RELEASE_IMMUTABLE" = "$VERIFIED_RELEASE_IMMUTABLE"',
    "test \"$(find dist -maxdepth 1 -type f -name '*.whl' | wc -l)\" -eq 1",
    "test \"$(find dist -maxdepth 1 -type f -name '*.tar.gz' | wc -l)\" -eq 1",
    "test \"$(find dist -maxdepth 1 -type f -name 'SHA256SUMS' | wc -l)\" -eq 1",
    'test "$(find dist -maxdepth 1 -type f | wc -l)" -eq 3',
)
ATTACH_IDENTITY_LINES = (
    "set -euo pipefail",
    'test "$BUILD_SOURCE_SHA" = "$VERIFIED_SOURCE_SHA"',
    'test -n "$ARTIFACT_ID"',
    '[[ "$ARTIFACT_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]',
    '[[ "$MANIFEST_DIGEST" =~ ^[0-9a-f]{64}$ ]]',
    'test "$(sha256sum release-assets/SHA256SUMS | awk \'{print $1}\')" = "$MANIFEST_DIGEST"',
    "(cd release-assets && sha256sum --check SHA256SUMS)",
    "TAG_SHA=$(gh api \"repos/$GITHUB_REPOSITORY/git/ref/tags/$TAG_NAME\" --jq '.object.sha')",
    'CURRENT_RELEASE_JSON=$(gh api "repos/$GITHUB_REPOSITORY/releases/tags/$TAG_NAME")',
    "CURRENT_RELEASE_ID=$(jq -r '.id' <<< \"$CURRENT_RELEASE_JSON\")",
    "RELEASE_TARGET=$(jq -r '.target_commitish' <<< \"$CURRENT_RELEASE_JSON\")",
    "CURRENT_RELEASE_DRAFT=$(jq -r '.draft' <<< \"$CURRENT_RELEASE_JSON\")",
    "CURRENT_RELEASE_PRERELEASE=$(jq -r '.prerelease' <<< \"$CURRENT_RELEASE_JSON\")",
    "CURRENT_RELEASE_IMMUTABLE=$(jq -r '.immutable // false' <<< \"$CURRENT_RELEASE_JSON\")",
    'test "$TAG_SHA" = "$VERIFIED_SOURCE_SHA"',
    'test "$RELEASE_TARGET" = "$VERIFIED_SOURCE_SHA"',
    'test "$CURRENT_RELEASE_ID" = "$VERIFIED_RELEASE_ID"',
    'test "$CURRENT_RELEASE_DRAFT" = "$VERIFIED_RELEASE_DRAFT"',
    'test "$CURRENT_RELEASE_PRERELEASE" = "$VERIFIED_RELEASE_PRERELEASE"',
    'test "$CURRENT_RELEASE_IMMUTABLE" = "$VERIFIED_RELEASE_IMMUTABLE"',
    "test \"$(find release-assets -maxdepth 1 -type f -name '*.whl' | wc -l)\" -eq 1",
    "test \"$(find release-assets -maxdepth 1 -type f -name '*.tar.gz' | wc -l)\" -eq 1",
    "test \"$(find release-assets -maxdepth 1 -type f -name 'SHA256SUMS' | wc -l)\" -eq 1",
    'test "$(find release-assets -maxdepth 1 -type f | wc -l)" -eq 3',
    (
        "EXISTING=$(gh api --paginate "
        '"repos/$GITHUB_REPOSITORY/releases/$CURRENT_RELEASE_ID/assets?per_page=100" '
        "--jq '.[].name')"
    ),
    "for asset in release-assets/*; do",
    'name=$(basename "$asset")',
    'if grep -Fqx "$name" <<< "$EXISTING"; then',
    'echo "::error::existing release asset refuses no-clobber publication: $name"',
    "exit 1",
    "fi",
    "done",
    'CURRENT_RELEASE_JSON=$(gh api "repos/$GITHUB_REPOSITORY/releases/tags/$TAG_NAME")',
    "CURRENT_RELEASE_ID=$(jq -r '.id' <<< \"$CURRENT_RELEASE_JSON\")",
    "RELEASE_TARGET=$(jq -r '.target_commitish' <<< \"$CURRENT_RELEASE_JSON\")",
    "CURRENT_RELEASE_DRAFT=$(jq -r '.draft' <<< \"$CURRENT_RELEASE_JSON\")",
    "CURRENT_RELEASE_PRERELEASE=$(jq -r '.prerelease' <<< \"$CURRENT_RELEASE_JSON\")",
    "CURRENT_RELEASE_IMMUTABLE=$(jq -r '.immutable // false' <<< \"$CURRENT_RELEASE_JSON\")",
    'test "$CURRENT_RELEASE_ID" = "$VERIFIED_RELEASE_ID"',
    'test "$RELEASE_TARGET" = "$VERIFIED_SOURCE_SHA"',
    'test "$CURRENT_RELEASE_DRAFT" = "$VERIFIED_RELEASE_DRAFT"',
    'test "$CURRENT_RELEASE_PRERELEASE" = "$VERIFIED_RELEASE_PRERELEASE"',
    'test "$CURRENT_RELEASE_IMMUTABLE" = "$VERIFIED_RELEASE_IMMUTABLE"',
    'gh release upload "$TAG_NAME" release-assets/* --repo "$GITHUB_REPOSITORY"',
)
LOCK_REGENERATE_RUN = 'python -m pip install "uv==0.11.19"\nuv lock\n'
LOCK_STATE_RUN = (
    "set -euo pipefail\n"
    'test "$(git rev-parse HEAD)" = "$EXPECTED_HEAD_SHA"\n'
    "REMOTE_HEAD=$(git ls-remote --exit-code origin \"refs/heads/$HEAD_REF\" | awk '{print $1}')\n"
    'test "$REMOTE_HEAD" = "$EXPECTED_HEAD_SHA"\n'
    "if git diff --quiet -- uv.lock; then\n"
    '  echo "uv.lock is already synchronized."\n'
    '  echo "changed=false" >> "$GITHUB_OUTPUT"\n'
    '  echo "source_sha=$EXPECTED_HEAD_SHA" >> "$GITHUB_OUTPUT"\n'
    "  exit 0\n"
    "fi\n"
    'test "$(git diff --name-only)" = "uv.lock"\n'
    'test -z "$(git diff --cached --name-only)"\n'
    'test -z "$(git ls-files --others --exclude-standard)"\n'
    "LOCK_SHA256=$(sha256sum uv.lock | awk '{print $1}')\n"
    '[[ "$LOCK_SHA256" =~ ^[0-9a-f]{64}$ ]]\n'
    'echo "changed=true" >> "$GITHUB_OUTPUT"\n'
    'echo "source_sha=$EXPECTED_HEAD_SHA" >> "$GITHUB_OUTPUT"\n'
    'echo "lock_sha256=$LOCK_SHA256" >> "$GITHUB_OUTPUT"\n'
)
LOCK_COMMIT_RUN = (
    "set -euo pipefail\n"
    'test "$EXPECTED_HEAD_SHA" = "${{ github.event.pull_request.head.sha }}"\n'
    'test "$(git rev-parse HEAD)" = "$EXPECTED_HEAD_SHA"\n'
    "REMOTE_HEAD=$(git ls-remote --exit-code origin \"refs/heads/$HEAD_REF\" | awk '{print $1}')\n"
    'test "$REMOTE_HEAD" = "$EXPECTED_HEAD_SHA"\n'
    '[[ "$EXPECTED_LOCK_SHA256" =~ ^[0-9a-f]{64}$ ]]\n'
    'test "$(sha256sum uv.lock | awk \'{print $1}\')" = "$EXPECTED_LOCK_SHA256"\n'
    'test "$(git diff --name-only)" = "uv.lock"\n'
    'test -z "$(git diff --cached --name-only)"\n'
    'test -z "$(git ls-files --others --exclude-standard)"\n'
    'git config user.name "loonghao"\n'
    'git config user.email "hal.long@outlook.com"\n'
    "git add uv.lock\n"
    'git commit -m "chore(ci): sync generated release lock"\n'
    'test "$(git rev-parse HEAD^)" = "$EXPECTED_HEAD_SHA"\n'
    'test "$(git diff-tree --no-commit-id --name-only -r HEAD)" = "uv.lock"\n'
)
LOCK_PUSH_RUN = (
    "set -euo pipefail\n"
    "AUTH_HEADER=$(printf 'x-access-token:%s' \"$WRITE_TOKEN\" | /usr/bin/base64 -w 0)\n"
    "unset WRITE_TOKEN\n"
    "/usr/bin/git -c core.hooksPath=/dev/null -c credential.helper= -c http.proxy= "
    '-c "http.https://github.com/.extraheader=AUTHORIZATION: basic $AUTH_HEADER" '
    'push "https://github.com/$GITHUB_REPOSITORY.git" "HEAD:refs/heads/$HEAD_REF"\n'
)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a mapping")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    return value


def _steps(job: Mapping[str, Any], label: str) -> list[Mapping[str, Any]]:
    return [
        _mapping(step, f"{label}.steps[{index}]")
        for index, step in enumerate(_list(job.get("steps"), f"{label}.steps"))
    ]


def _jobs(document: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(document.get("jobs"), "jobs")


def _job(document: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    return _mapping(_jobs(document).get(name), f"jobs.{name}")


def _all_steps(document: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    result: list[Mapping[str, Any]] = []
    for name, value in _jobs(document).items():
        result.extend(_steps(_mapping(value, f"jobs.{name}"), f"jobs.{name}"))
    return result


def _require_pinned_actions(document: Mapping[str, Any], label: str) -> None:
    for step in _all_steps(document):
        uses = step.get("uses")
        if uses is not None and (
            not isinstance(uses, str) or PINNED_ACTION.fullmatch(uses) is None
        ):
            raise ValueError(f"{label} action must use an exact commit pin: {uses!r}")


def _require_timeouts(document: Mapping[str, Any], label: str) -> None:
    for name, value in _jobs(document).items():
        job = _mapping(value, f"jobs.{name}")
        timeout = job.get("timeout-minutes")
        if not isinstance(timeout, int) or not (1 <= timeout <= 30):
            raise ValueError(f"{label} job {name} must have a bounded timeout")


def _step_by_name(steps: Sequence[Mapping[str, Any]], name: str) -> Mapping[str, Any]:
    matches = [step for step in steps if step.get("name") == name]
    if len(matches) != 1:
        raise ValueError(f"workflow must contain exactly one step named {name!r}")
    return matches[0]


def _step_markers(
    steps: Sequence[Mapping[str, Any]],
) -> list[tuple[Any, Any, Any]]:
    return [(step.get("name"), step.get("id"), step.get("uses")) for step in steps]


def _run(step: Mapping[str, Any], name: str) -> str:
    value = step.get("run")
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be an executable run step")
    return value


def _without_comments(value: str) -> str:
    return "\n".join(line for line in value.splitlines() if not line.lstrip().startswith("#"))


def _executable_lines(value: str) -> tuple[str, ...]:
    return tuple(
        line.strip()
        for line in value.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def _require_closed_shell(value: str, label: str, expected_lines: Sequence[str]) -> None:
    if _executable_lines(value) != tuple(expected_lines):
        raise ValueError(f"{label} must keep the reviewed closed mutation surface")


def validate_release(document: Mapping[str, Any]) -> None:
    if document.get("permissions") != {}:
        raise ValueError("release top-level permissions must be empty")
    jobs = _jobs(document)
    required_jobs = {
        "release-please",
        "verify-release-source",
        "build",
        "publish",
        "attach-release-assets",
    }
    if set(jobs) != required_jobs:
        raise ValueError("release workflow must expose only the reviewed release jobs")
    _require_pinned_actions(document, "release")
    _require_timeouts(document, "release")

    release_please = _job(document, "release-please")
    if release_please.get("permissions") != {"contents": "write", "pull-requests": "write"}:
        raise ValueError("release-please permissions must be minimal")
    verify = _job(document, "verify-release-source")
    if verify.get("permissions") != {"contents": "read"}:
        raise ValueError("source verification permissions must be read-only")
    build = _job(document, "build")
    if build.get("permissions") != {"contents": "read"}:
        raise ValueError("build permissions must be read-only")
    publish = _job(document, "publish")
    if publish.get("permissions") != {"actions": "read", "contents": "read", "id-token": "write"}:
        raise ValueError("PyPI permissions must be least privilege")
    attach = _job(document, "attach-release-assets")
    if attach.get("permissions") != {"actions": "read", "contents": "write"}:
        raise ValueError("GitHub asset permissions must be least privilege")
    if attach.get("needs") != ["release-please", "verify-release-source", "build", "publish"]:
        raise ValueError("GitHub assets must depend on successful PyPI publication")
    if attach.get("if") != (
        "needs.release-please.outputs.release_created == 'true' && "
        "needs.publish.result == 'success'"
    ):
        raise ValueError("GitHub assets must fail closed when PyPI publication fails")

    if _step_markers(_steps(release_please, "jobs.release-please")) != [
        (
            None,
            "release",
            "googleapis/release-please-action@45996ed1f6d02564a971a2fa1b5860e934307cf7",
        )
    ]:
        raise ValueError("release-please must keep its exact reviewed step")

    verify_outputs = _mapping(verify.get("outputs"), "verify-release-source.outputs")
    expected_verify_outputs = {
        "source_sha": "${{ steps.source.outputs.source_sha }}",
        "release_id": "${{ steps.source.outputs.release_id }}",
        "release_draft": "${{ steps.source.outputs.release_draft }}",
        "release_prerelease": "${{ steps.source.outputs.release_prerelease }}",
        "release_immutable": "${{ steps.source.outputs.release_immutable }}",
    }
    if verify_outputs != expected_verify_outputs:
        raise ValueError("source verification must freeze exact Release ID and state")

    build_outputs = _mapping(build.get("outputs"), "build.outputs")
    if build_outputs != {
        "source_sha": "${{ steps.source.outputs.source_sha }}",
        "artifact_id": "${{ steps.upload.outputs.artifact-id }}",
        "artifact_digest": "${{ steps.upload.outputs.artifact-digest }}",
        "manifest_digest": "${{ steps.hashes.outputs.manifest_digest }}",
    }:
        raise ValueError("build must expose source, artifact ID, and trusted hash manifest")
    build_steps = _steps(build, "jobs.build")
    upload = _step_by_name(build_steps, "Upload immutable Python distributions")
    if upload.get("uses") != UPLOAD_ARTIFACT or upload.get("id") != "upload":
        raise ValueError("build must upload exactly one immutable distribution artifact")
    upload_with = _mapping(upload.get("with"), "build upload inputs")
    upload_path = upload_with.get("path")
    if (
        not isinstance(upload_path, str)
        or "dist/*.whl" not in upload_path
        or "dist/*.tar.gz" not in upload_path
        or "dist/SHA256SUMS" not in upload_path
    ):
        raise ValueError("build artifact must contain distributions and their hash manifest")

    verify_steps = _steps(verify, "jobs.verify-release-source")
    if _step_markers(verify_steps) != [
        (None, None, CHECKOUT),
        ("Bind tag and GitHub Release to the checked-out source", "source", None),
    ]:
        raise ValueError("source verification must keep its reviewed ordered steps")
    source_step = _step_by_name(
        verify_steps, "Bind tag and GitHub Release to the checked-out source"
    )
    if source_step.get("id") != "source" or source_step.get("shell") != "bash":
        raise ValueError("source verification must keep its exact reviewed step binding")
    if source_step.get("env") != {
        "GH_HOST": "github.com",
        "GH_TOKEN": "${{ github.token }}",
        "TAG_NAME": "${{ needs.release-please.outputs.tag_name }}",
    }:
        raise ValueError("source verification must keep its exact reviewed environment")
    source_run = _run(source_step, "source verification")
    _require_closed_shell(source_run, "source verification", RELEASE_SOURCE_LINES)

    build_source_step = _step_by_name(build_steps, "Bind build to verified source")
    if build_source_step.get("id") != "source" or build_source_step.get("shell") != "bash":
        raise ValueError("build source must keep its exact reviewed step binding")
    if build_source_step.get("env") != {
        "VERIFIED_SOURCE_SHA": "${{ needs.verify-release-source.outputs.source_sha }}",
        "TAG_NAME": "${{ needs.release-please.outputs.tag_name }}",
    }:
        raise ValueError("build source must keep its exact reviewed environment")
    build_source_run = _run(build_source_step, "build source")
    _require_closed_shell(build_source_run, "build source", BUILD_SOURCE_LINES)
    hash_step = _step_by_name(build_steps, "Create trusted distribution hash manifest")
    if hash_step.get("id") != "hashes" or hash_step.get("shell") != "bash":
        raise ValueError("trusted hash manifest must keep its exact output binding")
    _require_closed_shell(
        _run(hash_step, "trusted hash manifest"),
        "trusted hash manifest",
        BUILD_HASH_LINES,
    )
    if _step_markers(build_steps) != [
        (None, None, CHECKOUT),
        (None, None, SETUP_PYTHON),
        ("Bind build to verified source", "source", None),
        ("Install release validation dependencies", None, None),
        ("Validate release contracts", None, None),
        ("Build wheel and sdist", None, None),
        ("Validate distributions", None, None),
        ("Create trusted distribution hash manifest", "hashes", None),
        ("Upload immutable Python distributions", "upload", UPLOAD_ARTIFACT),
        ("Record immutable build receipt", None, None),
    ]:
        raise ValueError("build must keep its reviewed ordered steps")

    publish_steps = _steps(publish, "jobs.publish")
    attach_steps = _steps(attach, "jobs.attach-release-assets")
    all_steps = _all_steps(document)
    pypi_steps = [step for step in all_steps if step.get("uses") == PYPI_PUBLISH]
    if len(pypi_steps) != 1:
        raise ValueError("release must contain exactly one PyPI publication mutation")
    executable_runs = [_without_comments(str(step.get("run", ""))) for step in all_steps]
    mutations = sum(len(re.findall(r"\bgh\s+release\s+upload\b", run)) for run in executable_runs)
    if mutations != 1:
        raise ValueError("release must contain exactly one GitHub Release upload mutation")
    if any("--clobber" in run for run in executable_runs):
        raise ValueError("GitHub Release publication must never clobber assets")

    if [step.get("name") for step in publish_steps] != [
        "Download immutable Python distributions",
        "Verify immutable identity immediately before PyPI",
        "Publish to PyPI with Trusted Publishing",
    ]:
        raise ValueError("PyPI job must keep the reviewed closed mutation surface")
    if [step.get("name") for step in attach_steps] != [
        "Download immutable Python distributions",
        "Verify identity and attach assets without clobbering",
    ]:
        raise ValueError("GitHub asset job must keep the reviewed closed mutation surface")

    for consumer, label in ((publish_steps[0], "PyPI"), (attach_steps[0], "GitHub assets")):
        if consumer.get("uses") != DOWNLOAD_ARTIFACT:
            raise ValueError(f"{label} must use the reviewed artifact downloader")
        inputs = _mapping(consumer.get("with"), f"{label} download inputs")
        expected_inputs = {
            "artifact-ids": BUILD_ARTIFACT_ID,
            "path": "dist" if label == "PyPI" else "release-assets",
            "merge-multiple": True,
        }
        if inputs != expected_inputs:
            raise ValueError(f"{label} must download only the exact build artifact ID")

    pypi_inputs = _mapping(pypi_steps[0].get("with"), "PyPI inputs")
    if pypi_inputs != {
        "packages-dir": "dist",
        "verbose": True,
        "print-hash": True,
    }:
        raise ValueError("PyPI publication must keep its exact fail-closed inputs")

    identity_env = {
        "GH_HOST": "github.com",
        "GH_TOKEN": "${{ github.token }}",
        "TAG_NAME": "${{ needs.release-please.outputs.tag_name }}",
        "BUILD_SOURCE_SHA": "${{ needs.build.outputs.source_sha }}",
        "VERIFIED_SOURCE_SHA": "${{ needs.verify-release-source.outputs.source_sha }}",
        "ARTIFACT_ID": BUILD_ARTIFACT_ID,
        "ARTIFACT_DIGEST": BUILD_ARTIFACT_DIGEST,
        "MANIFEST_DIGEST": BUILD_MANIFEST_DIGEST,
        "VERIFIED_RELEASE_ID": "${{ needs.verify-release-source.outputs.release_id }}",
        "VERIFIED_RELEASE_DRAFT": "${{ needs.verify-release-source.outputs.release_draft }}",
        "VERIFIED_RELEASE_PRERELEASE": (
            "${{ needs.verify-release-source.outputs.release_prerelease }}"
        ),
        "VERIFIED_RELEASE_IMMUTABLE": (
            "${{ needs.verify-release-source.outputs.release_immutable }}"
        ),
    }
    for step, label in ((publish_steps[1], "PyPI"), (attach_steps[1], "GitHub assets")):
        env = _mapping(step.get("env"), f"{label} identity env")
        if env != identity_env or step.get("shell") != "bash":
            raise ValueError(f"{label} must keep the exact identity step binding")
        identity = _run(step, f"{label} identity")
        for required in (
            "TAG_SHA=$(gh api",
            "CURRENT_RELEASE_ID=$(jq",
            "CURRENT_RELEASE_IMMUTABLE=$(jq",
            "sha256sum --check",
            "MANIFEST_DIGEST",
        ):
            if required not in identity:
                raise ValueError(
                    f"{label} must freshly recapture immutable identity before mutation"
                )

    _require_closed_shell(
        _run(publish_steps[1], "PyPI identity"),
        "PyPI identity",
        PYPI_IDENTITY_LINES,
    )
    _require_closed_shell(
        _run(attach_steps[1], "GitHub asset mutation"),
        "GitHub asset mutation",
        ATTACH_IDENTITY_LINES,
    )

    attach_run = _run(attach_steps[1], "GitHub asset mutation")
    upload_index = attach_run.index("gh release upload")
    if attach_run.rfind("CURRENT_RELEASE_ID=$(jq", 0, upload_index) < attach_run.index("done"):
        raise ValueError("GitHub Release identity must be recaptured immediately before mutation")


def validate_ci(document: Mapping[str, Any]) -> None:
    if document.get("permissions") != {"contents": "read"}:
        raise ValueError("CI permissions must be read-only")
    _require_pinned_actions(document, "CI")
    _require_timeouts(document, "CI")
    dependency = _job(document, "dependency-contract")
    matrix = _mapping(
        _mapping(dependency.get("strategy"), "dependency strategy").get("matrix"), "matrix"
    )
    if matrix.get("core-version") != ["0.20.14", "latest"]:
        raise ValueError("CI must test exact Core floor and latest allowed lanes")
    runs = "\n".join(str(step.get("run", "")) for step in _steps(dependency, "dependency-contract"))
    for required in (
        'CORE_REQUIREMENT="dcc-mcp-core>=0.20.14,<1.0.0"',
        'CORE_REQUIREMENT="dcc-mcp-core==$CORE_VERSION"',
        '"waapi-client==0.8.1"',
        "python scripts/ci/check_installed_dependencies.py",
    ):
        if required not in runs:
            raise ValueError("CI dependency lanes must verify Core and audited WAAPI resolution")
    all_runs = "\n".join(str(step.get("run", "")) for step in _all_steps(document))
    for required in (
        "python scripts/ci/check_uv_lock.py",
        "python scripts/ci/check_workflows.py",
        "uv lock --check",
    ):
        if required not in all_runs:
            raise ValueError("CI must execute all release contract checks")


def validate_lock_sync(document: Mapping[str, Any]) -> None:
    if document.get("permissions") != {}:
        raise ValueError("lock sync top-level permissions must be empty")
    if set(_jobs(document)) != {"generate-release-lock", "sync-release-lock"}:
        raise ValueError("lock sync must separate credential-free generation from fixed push")
    _require_pinned_actions(document, "lock sync")
    _require_timeouts(document, "lock sync")
    generate = _job(document, "generate-release-lock")
    push = _job(document, "sync-release-lock")
    if set(generate) != {
        "name",
        "runs-on",
        "timeout-minutes",
        "if",
        "permissions",
        "outputs",
        "steps",
    }:
        raise ValueError("lock generation must keep its exact credential-free job surface")
    if set(push) != {
        "name",
        "needs",
        "if",
        "runs-on",
        "timeout-minutes",
        "permissions",
        "steps",
    }:
        raise ValueError("lock push must keep its exact reviewed job surface")
    if (
        generate.get("name") != "Generate release lock without credentials"
        or generate.get("runs-on") != "ubuntu-latest"
    ):
        raise ValueError("lock generation must keep its exact credential-free job surface")
    if push.get("name") != "Sync generated release lock" or push.get("runs-on") != "ubuntu-latest":
        raise ValueError("lock push must keep its exact reviewed job surface")
    if generate.get("permissions") != {"contents": "read", "pull-requests": "read"}:
        raise ValueError("lock generation must be credential-free and read-only")
    if push.get("permissions") != {
        "actions": "read",
        "contents": "read",
        "pull-requests": "read",
    }:
        raise ValueError("lock push preparation must use read-only workflow permissions")
    expected_condition = (
        "github.event.pull_request.head.repo.full_name == github.repository && "
        "startsWith(github.event.pull_request.head.ref, "
        "'release-please--branches--main') && "
        "startsWith(github.event.pull_request.title, 'chore(main): release ')"
    )
    if generate.get("if") != expected_condition:
        raise ValueError("lock generation must keep its exact reviewed event condition")
    if push.get("needs") != "generate-release-lock" or push.get("if") != (
        "needs.generate-release-lock.outputs.changed == 'true'"
    ):
        raise ValueError("lock push must consume only a changed credential-free result")

    generate_steps = _steps(generate, "generate-release-lock")
    expected_generate_steps: list[Mapping[str, Any]] = [
        {
            "uses": CHECKOUT,
            "with": {
                "ref": "${{ github.event.pull_request.head.ref }}",
                "fetch-depth": 0,
                "persist-credentials": False,
            },
        },
        {"uses": SETUP_PYTHON, "with": {"python-version": "3.12"}},
        {
            "name": "Regenerate the exact release lock",
            "run": LOCK_REGENERATE_RUN,
        },
        {
            "name": "Recapture exact event, head, and diff",
            "id": "state",
            "env": {
                "EXPECTED_HEAD_SHA": "${{ github.event.pull_request.head.sha }}",
                "HEAD_REF": "${{ github.event.pull_request.head.ref }}",
            },
            "shell": "bash",
            "run": LOCK_STATE_RUN,
        },
        {
            "name": "Upload exact generated lock",
            "if": "steps.state.outputs.changed == 'true'",
            "id": "upload",
            "uses": UPLOAD_ARTIFACT,
            "with": {
                "name": (
                    "release-lock-${{ github.event.pull_request.number }}-${{ github.run_id }}"
                ),
                "path": "uv.lock",
                "if-no-files-found": "error",
                "compression-level": 0,
                "retention-days": 1,
            },
        },
    ]
    if generate_steps != expected_generate_steps:
        raise ValueError("lock generation must keep its credential-free closed execution surface")

    expected_outputs = {
        "changed": "${{ steps.state.outputs.changed }}",
        "source_sha": "${{ steps.state.outputs.source_sha }}",
        "lock_sha256": "${{ steps.state.outputs.lock_sha256 }}",
        "artifact_id": "${{ steps.upload.outputs.artifact-id }}",
    }
    if generate.get("outputs") != expected_outputs:
        raise ValueError("lock generation must expose only recaptured immutable outputs")

    push_steps = _steps(push, "sync-release-lock")
    expected_push_steps: list[Mapping[str, Any]] = [
        {
            "uses": CHECKOUT,
            "with": {
                "ref": "${{ needs.generate-release-lock.outputs.source_sha }}",
                "fetch-depth": 0,
                "persist-credentials": False,
            },
        },
        {
            "name": "Download exact generated lock",
            "uses": DOWNLOAD_ARTIFACT,
            "with": {
                "artifact-ids": "${{ needs.generate-release-lock.outputs.artifact_id }}",
                "path": ".",
            },
        },
        {
            "name": "Recapture and commit the exact lock only",
            "env": {
                "EXPECTED_HEAD_SHA": "${{ needs.generate-release-lock.outputs.source_sha }}",
                "EXPECTED_LOCK_SHA256": ("${{ needs.generate-release-lock.outputs.lock_sha256 }}"),
                "HEAD_REF": "${{ github.event.pull_request.head.ref }}",
            },
            "shell": "bash",
            "run": LOCK_COMMIT_RUN,
        },
        {
            "name": "Push the recaptured lock commit",
            "env": {
                "HEAD_REF": "${{ github.event.pull_request.head.ref }}",
                "WRITE_TOKEN": "${{ secrets.PERSONAL_ACCESS_TOKEN }}",
            },
            "shell": "bash",
            "run": LOCK_PUSH_RUN,
        },
    ]
    if push_steps != expected_push_steps:
        raise ValueError(
            "lock push reviewed ordered steps and closed mutation surface must expose "
            "the write credential only to the fixed final push"
        )

    secret_text = repr(document)
    if secret_text.count("secrets.PERSONAL_ACCESS_TOKEN") != 1:
        raise ValueError("lock sync write credential must appear only in the final push step")


def validate_version_consistency(document: Mapping[str, Any]) -> None:
    if document.get("permissions") != {"contents": "read", "pull-requests": "read"}:
        raise ValueError("version consistency permissions must be read-only")
    _require_pinned_actions(document, "version consistency")
    _require_timeouts(document, "version consistency")
    paths = _mapping(document.get("on"), "version consistency triggers").get("pull_request")
    watched = _mapping(paths, "version consistency pull_request").get("paths")
    watched_paths = _list(watched, "version consistency paths")
    for required in ("uv.lock", "release-please-config.json", "scripts/ci/check_uv_lock.py"):
        if required not in watched_paths:
            raise ValueError(f"version consistency must watch {required}")
    runs = "\n".join(str(step.get("run", "")) for step in _all_steps(document))
    if "python scripts/ci/check_uv_lock.py" not in runs or "uv lock --check" not in runs:
        raise ValueError("version consistency must execute lock and metadata validation")


def _load(relative: str) -> Mapping[str, Any]:
    path = ROOT / relative
    try:
        return _mapping(yaml_loads(path.read_text(encoding="utf-8")), relative)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(
            f"{relative} is unreadable or invalid YAML: {exc.__class__.__name__}"
        ) from None


def validate_all() -> None:
    validate_release(_load(".github/workflows/release.yml"))
    validate_ci(_load(".github/workflows/ci.yml"))
    validate_lock_sync(_load(".github/workflows/release-please-lock-sync.yml"))
    validate_version_consistency(_load(".github/workflows/version-consistency.yml"))


def main() -> int:
    try:
        validate_all()
    except ValueError as exc:
        print(f"workflow contract failed: {exc}", file=sys.stderr)
        return 1
    print("workflow contract passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
