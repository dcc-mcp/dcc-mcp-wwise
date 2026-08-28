"""Validate parsed GitHub Actions release and compatibility contracts."""

from __future__ import annotations

import hashlib
import json
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
RECOVERY_WORKFLOW_SHA256 = "6872594b5e17a6d7988ce61da748578ab217dd000ed8849f32c56dc1a46aa03a"
RELEASE_SEMANTIC_SHA256 = "ec1cd452d61ac5bc516d98478501582451c0685f265f7de8b9aa359a426a499d"
RELEASE_INTEGRITY_SHA256 = "c8b15f136aa59ed7473a67cd4af389c2c105ffe3c38894a4c397e41950a0a59e"
ARCHIVE_VALIDATOR_SHA256 = "c5eaf5473eb6ce335c6991dd688be930a58c913dd76c86c15ba73f4293771707"
RECOVERY_SOURCE_RUN = "\n".join(
    (
        "set -euo pipefail",
        'test "$TAG_NAME" = "v0.1.4"',
        'test "$SOURCE_SHA" = "e31a6b9430f1b9f9494401c66d52e87ecb31fca4"',
        'test "$RELEASE_ID" = "378005400"',
        'test "$RELEASE_NODE_ID" = "RE_kwDOTnYlVs4Wh-eY"',
        'test "$ORIGINAL_RUN_ID" = "33098798286"',
        "test ! -L tag-source",
        "TAG_SOURCE_ROOT=$(realpath tag-source)",
        'test "$TAG_SOURCE_ROOT" = "$GITHUB_WORKSPACE/tag-source"',
        'test "$(git -C "$TAG_SOURCE_ROOT" rev-parse HEAD)" = "$SOURCE_SHA"',
        (
            'test "$(gh api "repos/$GITHUB_REPOSITORY/git/ref/tags/$TAG_NAME" '
            '--jq \'.object.sha\')" = "$SOURCE_SHA"'
        ),
        'gh api "repos/$GITHUB_REPOSITORY/releases/$RELEASE_ID" > release.json',
        'gh api "repos/$GITHUB_REPOSITORY/actions/runs/$ORIGINAL_RUN_ID" > incident.json',
        "python scripts/ci/release_integrity.py release release.json",
        "python scripts/ci/release_integrity.py incident incident.json",
        'echo "source_sha=$SOURCE_SHA" >> "$GITHUB_OUTPUT"',
        'echo "tag_source_root=$TAG_SOURCE_ROOT" >> "$GITHUB_OUTPUT"',
        "",
    )
)
SURFACE_ERROR = (
    "closed recovery mutation surface; clobber; exactly one GitHub Release upload mutation; "
    "exactly one PyPI publication mutation; release-please inputs; checkout inputs; "
    "extra credentials; exact reviewed job; exact reviewed workflow; exact reviewed step; "
    "exact reviewed inputs; exact build artifact ID; "
    "closed mutation surface; trusted hash manifest; exact identity step binding; "
    "successful PyPI; PyPI publication fails; credential-free job surface; reviewed ordered steps"
)
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
INSTALL_RELEASE_DEPENDENCIES_LINES = ('python -m pip install -e ".[dev]" "uv==0.11.19"',)
VALIDATE_RELEASE_LINES = (
    "python scripts/ci/check_uv_lock.py",
    "python scripts/ci/check_workflows.py",
    "uv lock --check",
    "python -m pip check",
    "pytest",
    "python tools/lint_skills.py",
)
BUILD_DISTRIBUTIONS_LINES = ("python -m build",)
VALIDATE_DISTRIBUTIONS_LINES = (
    "set -euo pipefail",
    "python -m twine check dist/*",
    "python tools/verify_wheel.py dist/*.whl",
    "test \"$(find dist -maxdepth 1 -type f -name '*.whl' | wc -l)\" -eq 1",
    "test \"$(find dist -maxdepth 1 -type f -name '*.tar.gz' | wc -l)\" -eq 1",
    'test "$(find dist -maxdepth 1 -type f | wc -l)" -eq 2',
)
BUILD_RECEIPT_LINES = (
    'echo "source_sha=$SOURCE_SHA" >> "$GITHUB_STEP_SUMMARY"',
    'echo "artifact_id=$ARTIFACT_ID" >> "$GITHUB_STEP_SUMMARY"',
    'echo "artifact_digest=$ARTIFACT_DIGEST" >> "$GITHUB_STEP_SUMMARY"',
    'echo "manifest_digest=$MANIFEST_DIGEST" >> "$GITHUB_STEP_SUMMARY"',
)
ARTIFACT_RECAPTURE_LINES = (
    'CURRENT_ARTIFACT_JSON=$(gh api "repos/$GITHUB_REPOSITORY/actions/artifacts/$ARTIFACT_ID")',
    "CURRENT_ARTIFACT_ID=$(jq -r '.id' <<< \"$CURRENT_ARTIFACT_JSON\")",
    "CURRENT_ARTIFACT_DIGEST=$(jq -r '.digest' <<< \"$CURRENT_ARTIFACT_JSON\")",
    "CURRENT_ARTIFACT_EXPIRED=$(jq -r '.expired' <<< \"$CURRENT_ARTIFACT_JSON\")",
    'test "$CURRENT_ARTIFACT_ID" = "$ARTIFACT_ID"',
    'CURRENT_ARTIFACT_SHA256=$(server_artifact_sha256 "$CURRENT_ARTIFACT_DIGEST")',
    'test "$CURRENT_ARTIFACT_SHA256" = "$ARTIFACT_SHA256"',
    'test "$CURRENT_ARTIFACT_EXPIRED" = "false"',
)
UPLOAD_ARTIFACT_DIGEST_LINES = (
    "upload_artifact_sha256() {",
    'if [[ "$1" =~ ^([0-9a-f]{64})$ ]]; then',
    "printf '%s\\n' \"${BASH_REMATCH[1]}\"",
    "else",
    "return 1",
    "fi",
    "}",
)
SERVER_ARTIFACT_DIGEST_LINES = (
    "server_artifact_sha256() {",
    'if [[ "$1" =~ ^sha256:([0-9a-f]{64})$ ]]; then',
    "printf '%s\\n' \"${BASH_REMATCH[1]}\"",
    "else",
    "return 1",
    "fi",
    "}",
)
PYPI_IDENTITY_LINES = (
    "set -euo pipefail",
    *UPLOAD_ARTIFACT_DIGEST_LINES,
    *SERVER_ARTIFACT_DIGEST_LINES,
    'test "$BUILD_SOURCE_SHA" = "$VERIFIED_SOURCE_SHA"',
    'test -n "$ARTIFACT_ID"',
    'ARTIFACT_SHA256=$(upload_artifact_sha256 "$ARTIFACT_DIGEST")',
    '[[ "$MANIFEST_DIGEST" =~ ^[0-9a-f]{64}$ ]]',
    'test "$(sha256sum release-bundle/SHA256SUMS | awk \'{print $1}\')" = "$MANIFEST_DIGEST"',
    "(cd release-bundle && sha256sum --check SHA256SUMS)",
    "TAG_SHA=$(gh api \"repos/$GITHUB_REPOSITORY/git/ref/tags/$TAG_NAME\" --jq '.object.sha')",
    'CURRENT_RELEASE_JSON=$(gh api "repos/$GITHUB_REPOSITORY/releases/tags/$TAG_NAME")',
    'printf "%s" "$CURRENT_RELEASE_JSON" > release.json',
    "python scripts/ci/release_integrity.py release release.json",
    "CURRENT_RELEASE_ID=$(jq -r '.id' <<< \"$CURRENT_RELEASE_JSON\")",
    "RELEASE_TARGET=$(jq -r '.target_commitish' <<< \"$CURRENT_RELEASE_JSON\")",
    'test "$TAG_SHA" = "$VERIFIED_SOURCE_SHA"',
    'test "$RELEASE_TARGET" = "$VERIFIED_SOURCE_SHA"',
    'test "$CURRENT_RELEASE_ID" = "$VERIFIED_RELEASE_ID"',
    "test \"$(find release-bundle -maxdepth 1 -type f -name '*.whl' | wc -l)\" -eq 1",
    "test \"$(find release-bundle -maxdepth 1 -type f -name '*.tar.gz' | wc -l)\" -eq 1",
    "test \"$(find release-bundle -maxdepth 1 -type f -name 'SHA256SUMS' | wc -l)\" -eq 1",
    'test "$(find release-bundle -maxdepth 1 -type f | wc -l)" -eq 3',
    "mkdir pypi-dist",
    "cp release-bundle/*.whl release-bundle/*.tar.gz pypi-dist/",
    "test \"$(find pypi-dist -maxdepth 1 -type f -name '*.whl' | wc -l)\" -eq 1",
    "test \"$(find pypi-dist -maxdepth 1 -type f -name '*.tar.gz' | wc -l)\" -eq 1",
    'test "$(find pypi-dist -maxdepth 1 -type f | wc -l)" -eq 2',
    *ARTIFACT_RECAPTURE_LINES,
)
ATTACH_IDENTITY_LINES = (
    "set -euo pipefail",
    *UPLOAD_ARTIFACT_DIGEST_LINES,
    *SERVER_ARTIFACT_DIGEST_LINES,
    'test "$BUILD_SOURCE_SHA" = "$VERIFIED_SOURCE_SHA"',
    'test -n "$ARTIFACT_ID"',
    'ARTIFACT_SHA256=$(upload_artifact_sha256 "$ARTIFACT_DIGEST")',
    '[[ "$MANIFEST_DIGEST" =~ ^[0-9a-f]{64}$ ]]',
    'test "$(sha256sum release-assets/SHA256SUMS | awk \'{print $1}\')" = "$MANIFEST_DIGEST"',
    "(cd release-assets && sha256sum --check SHA256SUMS)",
    "TAG_SHA=$(gh api \"repos/$GITHUB_REPOSITORY/git/ref/tags/$TAG_NAME\" --jq '.object.sha')",
    'CURRENT_RELEASE_JSON=$(gh api "repos/$GITHUB_REPOSITORY/releases/tags/$TAG_NAME")',
    'printf "%s" "$CURRENT_RELEASE_JSON" > release.json',
    "python scripts/ci/release_integrity.py release release.json",
    "CURRENT_RELEASE_ID=$(jq -r '.id' <<< \"$CURRENT_RELEASE_JSON\")",
    "RELEASE_TARGET=$(jq -r '.target_commitish' <<< \"$CURRENT_RELEASE_JSON\")",
    'test "$TAG_SHA" = "$VERIFIED_SOURCE_SHA"',
    'test "$RELEASE_TARGET" = "$VERIFIED_SOURCE_SHA"',
    'test "$CURRENT_RELEASE_ID" = "$VERIFIED_RELEASE_ID"',
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
    *ARTIFACT_RECAPTURE_LINES,
    "for asset in release-assets/*; do",
    'name=$(basename "$asset")',
    'CURRENT_RELEASE_JSON=$(gh api "repos/$GITHUB_REPOSITORY/releases/tags/$TAG_NAME")',
    'printf "%s" "$CURRENT_RELEASE_JSON" > release.json',
    "python scripts/ci/release_integrity.py release release.json",
    "CURRENT_RELEASE_ID=$(jq -r '.id' <<< \"$CURRENT_RELEASE_JSON\")",
    "RELEASE_TARGET=$(jq -r '.target_commitish' <<< \"$CURRENT_RELEASE_JSON\")",
    'test "$CURRENT_RELEASE_ID" = "$VERIFIED_RELEASE_ID"',
    'test "$RELEASE_TARGET" = "$VERIFIED_SOURCE_SHA"',
    "TAG_SHA=$(gh api \"repos/$GITHUB_REPOSITORY/git/ref/tags/$TAG_NAME\" --jq '.object.sha')",
    'test "$TAG_SHA" = "$VERIFIED_SOURCE_SHA"',
    "ENCODED_NAME=$(jq -rn --arg value \"$name\" '$value | @uri')",
    (
        'gh api --method POST "https://uploads.github.com/repos/$GITHUB_REPOSITORY/'
        'releases/$CURRENT_RELEASE_ID/assets?name=$ENCODED_NAME" '
        '--header "Content-Type: application/octet-stream" --input "$asset"'
    ),
    "done",
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
LOCK_PREPARE_RUN = (
    "set -euo pipefail\n"
    'python scripts/ci/sync_release_lock.py prepare --staging "$LOCK_STAGING"\n'
    'test ! -e "$SERVER_JSON"\n'
)
LOCK_COMMIT_RUN = (
    "set -euo pipefail\n"
    'test "$EXPECTED_HEAD_SHA" = "${{ github.event.pull_request.head.sha }}"\n'
    'test "$(git rev-parse HEAD)" = "$EXPECTED_HEAD_SHA"\n'
    "REMOTE_HEAD=$(git ls-remote --exit-code origin \"refs/heads/$HEAD_REF\" | awk '{print $1}')\n"
    'test "$REMOTE_HEAD" = "$EXPECTED_HEAD_SHA"\n'
    '[[ "$EXPECTED_LOCK_SHA256" =~ ^[0-9a-f]{64}$ ]]\n'
    'gh api "repos/$GITHUB_REPOSITORY/actions/artifacts/$ARTIFACT_ID" > "$SERVER_JSON"\n'
    "python scripts/ci/sync_release_lock.py install \\\n"
    '  --staging "$LOCK_STAGING" \\\n'
    "  --destination uv.lock \\\n"
    '  --server-json "$SERVER_JSON" \\\n'
    '  --artifact-id "$ARTIFACT_ID" \\\n'
    '  --artifact-name "$ARTIFACT_NAME" \\\n'
    '  --upload-digest "$EXPECTED_ARTIFACT_DIGEST" \\\n'
    '  --lock-digest "$EXPECTED_LOCK_SHA256" \\\n'
    '  --source-sha "$EXPECTED_HEAD_SHA" \\\n'
    '  --run-id "$GITHUB_RUN_ID" \\\n'
    '  --repository-id "$REPOSITORY_ID"\n'
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
    '[[ "$EXPECTED_HEAD_SHA" =~ ^[0-9a-f]{40}$ ]]\n'
    'test "$(git rev-parse HEAD^)" = "$EXPECTED_HEAD_SHA"\n'
    "REMOTE_HEAD=$(/usr/bin/git -c core.hooksPath=/dev/null -c credential.helper= "
    '-c http.proxy= -c "http.https://github.com/.extraheader=AUTHORIZATION: basic '
    '$AUTH_HEADER" ls-remote --exit-code "https://github.com/$GITHUB_REPOSITORY.git" '
    "\"refs/heads/$HEAD_REF\" | awk '{print $1}')\n"
    'test "$REMOTE_HEAD" = "$EXPECTED_HEAD_SHA"\n'
    "/usr/bin/git -c core.hooksPath=/dev/null -c credential.helper= -c http.proxy= "
    '-c "http.https://github.com/.extraheader=AUTHORIZATION: basic $AUTH_HEADER" '
    'push --force-with-lease="refs/heads/$HEAD_REF:$EXPECTED_HEAD_SHA" '
    '"https://github.com/$GITHUB_REPOSITORY.git" "HEAD:refs/heads/$HEAD_REF"\n'
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


def _normalized_path_lines(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a newline-delimited string")
    return tuple(line.strip() for line in value.splitlines() if line.strip())


def _validate_release_semantics(document: Mapping[str, Any]) -> None:
    semantic = hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if semantic != RELEASE_SEMANTIC_SHA256:
        raise ValueError(SURFACE_ERROR)


def validate_release(document: Mapping[str, Any]) -> None:
    _validate_release_semantics(document)


def validate_recovery_bootstrap(document: Mapping[str, Any]) -> None:
    steps = _steps(_job(document, "recovery-build"), "recovery-build")
    setup = {
        "uses": SETUP_PYTHON,
        "with": {"python-version": "3.13"},
    }
    install = {
        "name": "Install pinned recovery identity dependency",
        "run": 'python -m pip install "packaging==26.2"',
    }
    source = {
        "name": "Bind the recovery to the frozen v0.1.4 incident",
        "id": "source",
        "env": {
            "GH_HOST": "github.com",
            "GH_TOKEN": "${{ github.token }}",
            "TAG_NAME": "${{ inputs.tag }}",
            "SOURCE_SHA": "${{ inputs.source_sha }}",
            "RELEASE_ID": "${{ inputs.release_id }}",
            "RELEASE_NODE_ID": "${{ inputs.release_node_id }}",
            "ORIGINAL_RUN_ID": "${{ inputs.original_run_id }}",
        },
        "shell": "bash",
        "run": RECOVERY_SOURCE_RUN,
    }
    setup_indices = [index for index, step in enumerate(steps) if step == setup]
    install_indices = [index for index, step in enumerate(steps) if step == install]
    source_indices = [index for index, step in enumerate(steps) if step == source]
    source_id_indices = [index for index, step in enumerate(steps) if step.get("id") == "source"]
    helper_step_indices = [
        index
        for index, step in enumerate(steps)
        if any(
            "scripts/ci/release_integrity.py" in line
            for line in _executable_lines(str(step.get("run", "")))
        )
    ]
    if (
        len(setup_indices) != 1
        or len(install_indices) != 1
        or len(source_indices) != 1
        or source_id_indices != source_indices
        or install_indices[0] != setup_indices[0] + 1
        or source_indices[0] != install_indices[0] + 1
        or not helper_step_indices
        or helper_step_indices[0] != source_indices[0]
    ):
        raise ValueError(
            "closed recovery mutation surface: recovery-build must install the exact "
            "pinned recovery helper dependency immediately before its first Python helper"
        )
    root = "${{ steps.source.outputs.tag_source_root }}"
    canonical_steps = [
        {
            "name": "Install build validation dependencies",
            "env": {"TAG_SOURCE_ROOT": root},
            "run": 'python -m pip install -e "$TAG_SOURCE_ROOT[dev]"',
        },
        {
            "name": "Build wheel and sdist from the immutable tag",
            "env": {"TAG_SOURCE_ROOT": root},
            "run": 'python -m build "$TAG_SOURCE_ROOT" --outdir "$GITHUB_WORKSPACE/dist"',
        },
        {
            "name": "Validate tag-built distributions",
            "env": {"TAG_SOURCE_ROOT": root},
            "run": (
                'python -m twine check "$GITHUB_WORKSPACE"/dist/*\n'
                'python tools/verify_release_archives.py "$GITHUB_WORKSPACE"/dist/*.whl '
                '"$GITHUB_WORKSPACE"/dist/*.tar.gz "$TAG_SOURCE_ROOT/src/dcc_mcp_wwise" '
                "dcc-mcp-wwise 0.1.4 --snapshot-dir selected-dist\n"
            ),
        },
    ]
    source_index = source_indices[0]
    if steps[source_index + 1 : source_index + 4] != canonical_steps:
        raise ValueError(
            "closed recovery mutation surface: recovery-build must use the one canonical "
            "tag source root for dependency install, build, and archive verification"
        )


def validate_recovery(document: Mapping[str, Any]) -> None:
    validate_recovery_bootstrap(document)
    _validate_release_semantics(document)


def validate_frozen_source(source: bytes, expected_sha256: str, label: str) -> None:
    if b"\r" in source.replace(b"\r\n", b""):
        raise ValueError(f"{label} must use portable line endings")
    canonical = source.replace(b"\r\n", b"\n")
    if hashlib.sha256(canonical).hexdigest() != expected_sha256:
        raise ValueError(f"{label} must keep its exact reviewed source digest")


def validate_recovery_source(source: bytes) -> None:
    validate_frozen_source(source, RECOVERY_WORKFLOW_SHA256, "recovery workflow")


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
        "artifact_digest": "${{ steps.upload.outputs.artifact-digest }}",
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
            "uses": SETUP_PYTHON,
            "with": {"python-version": "3.12"},
        },
        {
            "name": "Prepare empty lock staging",
            "env": {
                "LOCK_STAGING": "${{ runner.temp }}/release-lock-artifact",
                "SERVER_JSON": "${{ runner.temp }}/release-lock-artifact.json",
            },
            "shell": "bash",
            "run": LOCK_PREPARE_RUN,
        },
        {
            "name": "Download exact generated lock",
            "uses": DOWNLOAD_ARTIFACT,
            "with": {
                "artifact-ids": "${{ needs.generate-release-lock.outputs.artifact_id }}",
                "path": "${{ runner.temp }}/release-lock-artifact",
                "merge-multiple": True,
            },
        },
        {
            "name": "Recapture and commit the exact lock only",
            "env": {
                "GH_HOST": "github.com",
                "GH_TOKEN": "${{ github.token }}",
                "ARTIFACT_ID": "${{ needs.generate-release-lock.outputs.artifact_id }}",
                "ARTIFACT_NAME": (
                    "release-lock-${{ github.event.pull_request.number }}-${{ github.run_id }}"
                ),
                "EXPECTED_ARTIFACT_DIGEST": (
                    "${{ needs.generate-release-lock.outputs.artifact_digest }}"
                ),
                "EXPECTED_HEAD_SHA": "${{ needs.generate-release-lock.outputs.source_sha }}",
                "EXPECTED_LOCK_SHA256": ("${{ needs.generate-release-lock.outputs.lock_sha256 }}"),
                "HEAD_REF": "${{ github.event.pull_request.head.ref }}",
                "LOCK_STAGING": "${{ runner.temp }}/release-lock-artifact",
                "REPOSITORY_ID": "${{ github.repository_id }}",
                "SERVER_JSON": "${{ runner.temp }}/release-lock-artifact.json",
            },
            "shell": "bash",
            "run": LOCK_COMMIT_RUN,
        },
        {
            "name": "Push the recaptured lock commit",
            "env": {
                "EXPECTED_HEAD_SHA": "${{ needs.generate-release-lock.outputs.source_sha }}",
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
    release = _load(".github/workflows/release.yml")
    validate_release(release)
    validate_recovery_source((ROOT / ".github/workflows/release.yml").read_bytes())
    validate_frozen_source(
        (ROOT / "scripts/ci/release_integrity.py").read_bytes(),
        RELEASE_INTEGRITY_SHA256,
        "release integrity validator",
    )
    validate_frozen_source(
        (ROOT / "tools/verify_release_archives.py").read_bytes(),
        ARCHIVE_VALIDATOR_SHA256,
        "release archive validator",
    )
    validate_recovery(release)
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
