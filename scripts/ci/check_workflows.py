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
CHECKOUT = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
SETUP_PYTHON = "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97"
UPLOAD_ARTIFACT = "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"
DOWNLOAD_ARTIFACT = "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093"
PYPI_PUBLISH = "pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33"


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


def _run(step: Mapping[str, Any], name: str) -> str:
    value = step.get("run")
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be an executable run step")
    return value


def _without_comments(value: str) -> str:
    return "\n".join(line for line in value.splitlines() if not line.lstrip().startswith("#"))


def _require_closed_identity_shell(value: str, label: str, *, allow_release_upload: bool) -> None:
    exact_lines = {
        "set -euo pipefail",
        'test "$BUILD_SOURCE_SHA" = "$VERIFIED_SOURCE_SHA"',
        'test -n "$ARTIFACT_ID"',
        '[[ "$ARTIFACT_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]',
        'test "$TAG_SHA" = "$VERIFIED_SOURCE_SHA"',
        'test "$RELEASE_TARGET" = "$VERIFIED_SOURCE_SHA"',
        "test \"$(find dist -maxdepth 1 -type f -name '*.whl' | wc -l)\" -eq 1",
        "test \"$(find dist -maxdepth 1 -type f -name '*.tar.gz' | wc -l)\" -eq 1",
        'test "$(find dist -maxdepth 1 -type f | wc -l)" -eq 2',
        "test \"$(find release-assets -maxdepth 1 -type f -name '*.whl' | wc -l)\" -eq 1",
        "test \"$(find release-assets -maxdepth 1 -type f -name '*.tar.gz' | wc -l)\" -eq 1",
        'test "$(find release-assets -maxdepth 1 -type f | wc -l)" -eq 2',
        "for asset in release-assets/*; do",
        'name=$(basename "$asset")',
        'if grep -Fqx "$name" <<< "$EXISTING"; then',
        'echo "::error::existing release asset refuses no-clobber publication: $name"',
        "exit 1",
        "fi",
        "done",
    }
    allowed_prefixes = (
        'TAG_SHA=$(gh api "repos/$GITHUB_REPOSITORY/git/ref/tags/$TAG_NAME"',
        'RELEASE_ID=$(gh api "repos/$GITHUB_REPOSITORY/releases/tags/$TAG_NAME"',
        'RELEASE_TARGET=$(gh api "repos/$GITHUB_REPOSITORY/releases/tags/$TAG_NAME"',
        'EXISTING=$(gh api --paginate "repos/$GITHUB_REPOSITORY/releases/$RELEASE_ID/assets',
    )
    upload = 'gh release upload "$TAG_NAME" release-assets/* --repo "$GITHUB_REPOSITORY"'
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line in exact_lines or any(line.startswith(prefix) for prefix in allowed_prefixes):
            continue
        if allow_release_upload and line == upload:
            continue
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

    build_outputs = _mapping(build.get("outputs"), "build.outputs")
    if set(build_outputs) != {"source_sha", "artifact_id", "artifact_digest"}:
        raise ValueError("build must expose source, artifact ID, and content digest")
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
    ):
        raise ValueError("build artifact must contain the wheel and sdist")

    verify_steps = _steps(verify, "jobs.verify-release-source")
    source_run = _run(
        _step_by_name(verify_steps, "Bind tag and GitHub Release to the checked-out source"),
        "source verification",
    )
    for required in (
        'git fetch --force origin "refs/tags/$TAG_NAME:refs/tags/$TAG_NAME"',
        "SOURCE_SHA=$(git rev-parse HEAD)",
        'test "$TAG_SHA" = "$SOURCE_SHA"',
        'test "$RELEASE_TARGET" = "$SOURCE_SHA"',
    ):
        if required not in source_run:
            raise ValueError(
                "source verification must freshly bind tag, Release, and checked-out HEAD"
            )

    build_source_run = _run(
        _step_by_name(build_steps, "Bind build to verified source"), "build source"
    )
    if 'git fetch --force origin "refs/tags/$TAG_NAME:refs/tags/$TAG_NAME"' not in build_source_run:
        raise ValueError("build must freshly fetch the tag before comparing checked-out HEAD")
    if 'test "$SOURCE_SHA" = "$VERIFIED_SOURCE_SHA"' not in build_source_run:
        raise ValueError("build must bind actual checked-out HEAD to the verified source")

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
        if inputs.get("artifact-ids") != BUILD_ARTIFACT_ID:
            raise ValueError(f"{label} must download the exact build artifact ID")
        if inputs.get("merge-multiple") is not True:
            raise ValueError(f"{label} must expose the exact artifact files directly")

    pypi_inputs = _mapping(pypi_steps[0].get("with"), "PyPI inputs")
    if pypi_inputs.get("skip-existing") not in (None, False):
        raise ValueError("PyPI publication must fail rather than overwrite or skip existing files")

    for step, label in ((publish_steps[1], "PyPI"), (attach_steps[1], "GitHub assets")):
        env = _mapping(step.get("env"), f"{label} identity env")
        if (
            env.get("ARTIFACT_ID") != BUILD_ARTIFACT_ID
            or env.get("ARTIFACT_DIGEST") != BUILD_ARTIFACT_DIGEST
        ):
            raise ValueError(f"{label} must bind the build artifact ID and content digest")
        identity = _run(step, f"{label} identity")
        for required in ("TAG_SHA=$(gh api", "RELEASE_TARGET=$(gh api", "ARTIFACT_DIGEST"):
            if required not in identity:
                raise ValueError(
                    f"{label} must freshly recapture immutable identity before mutation"
                )

    _require_closed_identity_shell(
        _run(publish_steps[1], "PyPI identity"), "PyPI identity", allow_release_upload=False
    )
    _require_closed_identity_shell(
        _run(attach_steps[1], "GitHub asset mutation"),
        "GitHub asset mutation",
        allow_release_upload=True,
    )

    attach_run = _run(attach_steps[1], "GitHub asset mutation")
    if attach_run.index("TAG_SHA=$(gh api") > attach_run.index("gh release upload"):
        raise ValueError("GitHub identity recapture must precede the upload mutation")


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
    _require_pinned_actions(document, "lock sync")
    _require_timeouts(document, "lock sync")
    job = _job(document, "sync-release-lock")
    if job.get("permissions") != {"contents": "write", "pull-requests": "read"}:
        raise ValueError("lock sync permissions must be minimal")
    runs = "\n".join(str(step.get("run", "")) for step in _steps(job, "sync-release-lock"))
    for required in ('python -m pip install "uv==0.11.19"', "uv lock", "git add uv.lock"):
        if required not in runs:
            raise ValueError("release-please lock sync must regenerate and stage only uv.lock")


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
