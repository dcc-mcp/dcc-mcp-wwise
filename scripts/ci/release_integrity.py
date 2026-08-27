"""Fail-closed release provenance and registry identity validation."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from packaging.utils import canonicalize_name

BARE_SHA256 = re.compile(r"([0-9a-f]{64})\Z")
SERVER_ARTIFACT_SHA256 = re.compile(r"sha256:([0-9a-f]{64})\Z")
RELEASE_NODE_ID = re.compile(r"RE_[A-Za-z0-9_-]+\Z")
MAX_GITHUB_INTEGER = (1 << 63) - 1


@dataclass(frozen=True)
class ArtifactIdentity:
    artifact_id: int
    node_id: str | None
    name: str
    sha256: str
    run_id: int
    repository_id: int
    head_repository_id: int
    head_sha: str


@dataclass(frozen=True)
class IncidentIdentity:
    run_id: int
    node_id: str
    name: str
    path: str
    event: str
    attempt: int
    workflow_id: int
    repository_id: int
    repository_owner: str
    repository_name: str
    repository_full_name: str
    head_sha: str


@dataclass(frozen=True)
class ReleaseIdentity:
    release_id: int
    node_id: str
    name: str
    tag: str
    target: str
    draft: bool
    prerelease: bool
    immutable: bool


def _bare_sha256(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    match = BARE_SHA256.fullmatch(value)
    if match is None:
        raise ValueError(f"{label} must be exactly 64 lowercase hexadecimal characters")
    return match.group(1)


def upload_artifact_sha256(value: object) -> str:
    """Parse only the upload-artifact action's bare digest output."""
    return _bare_sha256(value, "upload artifact SHA-256")


def server_artifact_sha256(value: object) -> str:
    """Parse only the GitHub artifact REST API's prefixed digest."""
    if not isinstance(value, str):
        raise ValueError("server artifact SHA-256 must be a string")
    match = SERVER_ARTIFACT_SHA256.fullmatch(value)
    if match is None:
        raise ValueError("server artifact SHA-256 must use canonical sha256:<64hex> format")
    return match.group(1)


def _pypi_sha256(value: object) -> str:
    return _bare_sha256(value, "PyPI SHA-256")


def _object(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _positive_int(value: object, label: str) -> int:
    if type(value) is not int or not 0 < value <= MAX_GITHUB_INTEGER:
        raise ValueError(f"{label} must be an exact positive integer")
    return value


def _required_string(value: object, label: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{label} must be an exact non-empty string")
    return value


def _load(path: Path) -> Mapping[str, Any]:
    return _object(json.loads(path.read_text(encoding="utf-8")), path.name)


def verify_artifact(path: Path, expected: ArtifactIdentity, *, require_live: bool) -> None:
    payload = _load(path)
    run = _object(payload.get("workflow_run"), "artifact workflow_run")
    actual = ArtifactIdentity(
        artifact_id=_positive_int(payload.get("id"), "artifact identity fields: artifact id"),
        node_id=payload.get("node_id"),
        name=payload.get("name"),
        sha256=server_artifact_sha256(payload.get("digest")),
        run_id=_positive_int(run.get("id"), "artifact identity fields: run id"),
        repository_id=_positive_int(
            run.get("repository_id"), "artifact identity fields: repository id"
        ),
        head_repository_id=_positive_int(
            run.get("head_repository_id"), "artifact identity fields: head repository id"
        ),
        head_sha=run.get("head_sha"),
    )
    if expected.node_id is None:
        actual = ArtifactIdentity(
            artifact_id=actual.artifact_id,
            node_id=None,
            name=actual.name,
            sha256=actual.sha256,
            run_id=actual.run_id,
            repository_id=actual.repository_id,
            head_repository_id=actual.head_repository_id,
            head_sha=actual.head_sha,
        )
    if actual != expected:
        raise ValueError("artifact provenance does not match the frozen identity")
    expired = payload.get("expired")
    if not isinstance(expired, bool):
        raise ValueError("artifact expired state must be boolean")
    if require_live and expired:
        raise ValueError("artifact is expired")


def _repository_identity(value: object) -> tuple[int, str, str, str]:
    repository = _object(value, "repository")
    owner = _object(repository.get("owner"), "repository owner")
    return (
        _positive_int(repository.get("id"), "repository id"),
        _required_string(owner.get("login"), "repository owner login"),
        _required_string(repository.get("name"), "repository name"),
        _required_string(repository.get("full_name"), "repository full name"),
    )


def verify_incident(path: Path, expected: IncidentIdentity) -> None:
    payload = _load(path)
    repository = _repository_identity(payload.get("repository"))
    head_repository = _repository_identity(payload.get("head_repository"))
    actual = IncidentIdentity(
        run_id=_positive_int(payload.get("id"), "incident identity fields: run id"),
        node_id=_required_string(payload.get("node_id"), "incident identity fields: node id"),
        name=_required_string(payload.get("name"), "incident identity fields: name"),
        path=_required_string(payload.get("path"), "incident identity fields: path"),
        event=_required_string(payload.get("event"), "incident identity fields: event"),
        attempt=_positive_int(payload.get("run_attempt"), "incident identity fields: run attempt"),
        workflow_id=_positive_int(
            payload.get("workflow_id"), "incident identity fields: workflow id"
        ),
        repository_id=repository[0],
        repository_owner=repository[1],
        repository_name=repository[2],
        repository_full_name=repository[3],
        head_sha=_required_string(payload.get("head_sha"), "incident identity fields: head SHA"),
    )
    if actual != expected:
        raise ValueError("release incident does not match the frozen identity")
    expected_repository = (
        expected.repository_id,
        expected.repository_owner,
        expected.repository_name,
        expected.repository_full_name,
    )
    if head_repository != expected_repository:
        raise ValueError("release incident head repository is not the frozen repository")
    if (
        _required_string(payload.get("status"), "incident status") != "completed"
        or _required_string(payload.get("conclusion"), "incident conclusion") != "failure"
    ):
        raise ValueError("release incident is not the frozen terminal failure")


def _release_state(payload: Mapping[str, Any]) -> tuple[bool, bool, bool]:
    fields = ("draft", "prerelease", "immutable")
    for field in fields:
        if field not in payload or type(payload[field]) is not bool:
            raise ValueError("release state fields must be present exact booleans")
    state = (payload["draft"], payload["prerelease"], payload["immutable"])
    if state != (False, False, False):
        raise ValueError("release state must be exactly false/false/false")
    return state


def _release_identity(path: Path) -> ReleaseIdentity:
    payload = _load(path)
    draft, prerelease, immutable = _release_state(payload)
    node_id = _required_string(payload.get("node_id"), "release node id")
    if RELEASE_NODE_ID.fullmatch(node_id) is None:
        raise ValueError("release node id must use the exact GitHub base64url form")
    return ReleaseIdentity(
        release_id=_positive_int(payload.get("id"), "release id"),
        node_id=node_id,
        name=_required_string(payload.get("name"), "release name"),
        tag=_required_string(payload.get("tag_name"), "release tag"),
        target=_required_string(payload.get("target_commitish"), "release target"),
        draft=draft,
        prerelease=prerelease,
        immutable=immutable,
    )


def verify_release(path: Path, expected: ReleaseIdentity) -> None:
    actual = _release_identity(path)
    if actual != expected:
        raise ValueError("release does not match the frozen identity")


def capture_release(
    path: Path, *, expected_name: str, expected_tag: str, expected_target: str
) -> ReleaseIdentity:
    actual = _release_identity(path)
    if (
        actual.name != expected_name
        or actual.tag != expected_tag
        or actual.target != expected_target
    ):
        raise ValueError("release does not match the captured identity")
    return actual


def _distribution_kind(path: Path) -> tuple[str, str]:
    if path.name.endswith(".whl"):
        return "bdist_wheel", "py3"
    if path.name.endswith(".tar.gz"):
        return "sdist", "source"
    raise ValueError("publication directory contains a non-distribution file")


def verify_pypi_release(path: Path, distributions: Path, *, project: str, version: str) -> None:
    payload = _load(path)
    info = _object(payload.get("info"), "PyPI info")
    if canonicalize_name(str(info.get("name"))) != canonicalize_name(project):
        raise ValueError("PyPI project name does not match")
    if info.get("version") != version:
        raise ValueError("PyPI version does not match")

    local = {item.name: item for item in distributions.iterdir() if item.is_file()}
    if len(local) != 2 or {
        kind for item in local.values() for kind, _ in [_distribution_kind(item)]
    } != {
        "bdist_wheel",
        "sdist",
    }:
        raise ValueError("publication directory must contain exactly one wheel and one sdist")
    urls = payload.get("urls")
    if not isinstance(urls, list) or len(urls) != 2:
        raise ValueError("PyPI release must contain exactly two files")
    records: dict[str, Mapping[str, Any]] = {}
    for value in urls:
        record = _object(value, "PyPI file")
        filename = record.get("filename")
        if not isinstance(filename, str) or filename in records:
            raise ValueError("PyPI filenames must be unique strings")
        records[filename] = record
    if set(records) != set(local):
        raise ValueError("PyPI filenames do not match local distributions")

    for filename, local_path in local.items():
        record = records[filename]
        package_type, python_version = _distribution_kind(local_path)
        if record.get("packagetype") != package_type:
            raise ValueError("PyPI distribution type does not match its filename")
        if record.get("python_version") != python_version:
            raise ValueError("PyPI Python version marker does not match")
        if record.get("yanked") is not False or record.get("yanked_reason") is not None:
            raise ValueError("PyPI distribution is yanked")
        size = record.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise ValueError("PyPI distribution size must be positive")
        if size != local_path.stat().st_size:
            raise ValueError("PyPI distribution size does not match local bytes")
        digests = _object(record.get("digests"), "PyPI digests")
        local_sha256 = hashlib.sha256(local_path.read_bytes()).hexdigest()
        if _pypi_sha256(digests.get("sha256")) != local_sha256:
            raise ValueError("PyPI SHA-256 does not match local bytes")
        uploaded = record.get("upload_time_iso_8601")
        if not isinstance(uploaded, str):
            raise ValueError("PyPI upload timestamp is missing")
        try:
            timestamp = datetime.fromisoformat(uploaded.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("PyPI upload timestamp is malformed") from error
        if timestamp.tzinfo is None:
            raise ValueError("PyPI upload timestamp must include a timezone")


INCIDENT = IncidentIdentity(
    run_id=33098798286,
    node_id="WFR_kwLOTnYlVs8AAAAHtNeUzg",
    name="Release",
    path=".github/workflows/release.yml",
    event="push",
    attempt=1,
    workflow_id=331601345,
    repository_id=1316365654,
    repository_owner="dcc-mcp",
    repository_name="dcc-mcp-wwise",
    repository_full_name="dcc-mcp/dcc-mcp-wwise",
    head_sha="e31a6b9430f1b9f9494401c66d52e87ecb31fca4",
)
RELEASE = ReleaseIdentity(
    release_id=378005400,
    node_id="RE_kwDOTnYlVs4Wh-eY",
    name="v0.1.4",
    tag="v0.1.4",
    target="e31a6b9430f1b9f9494401c66d52e87ecb31fca4",
    draft=False,
    prerelease=False,
    immutable=False,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    incident = subparsers.add_parser("incident")
    incident.add_argument("json", type=Path)
    release = subparsers.add_parser("release")
    release.add_argument("json", type=Path)
    release.add_argument("--id", type=int)
    release.add_argument("--node-id")
    release.add_argument("--name")
    release.add_argument("--tag")
    release.add_argument("--target")
    release.add_argument("--github-output", type=Path)
    artifact = subparsers.add_parser("artifact")
    artifact.add_argument("json", type=Path)
    artifact.add_argument("--id", type=int, required=True)
    artifact.add_argument("--name", required=True)
    artifact.add_argument("--sha256", required=True)
    artifact.add_argument("--run-id", type=int, required=True)
    artifact.add_argument("--repository-id", type=int, required=True)
    artifact.add_argument("--head-sha", required=True)
    pypi = subparsers.add_parser("pypi")
    pypi.add_argument("json", type=Path)
    pypi.add_argument("distributions", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.command == "incident":
        verify_incident(arguments.json, INCIDENT)
    elif arguments.command == "release":
        identity_values = (
            arguments.id,
            arguments.node_id,
            arguments.name,
            arguments.tag,
            arguments.target,
        )
        if arguments.github_output is not None:
            if arguments.id is not None or arguments.node_id is not None:
                raise ValueError("release capture cannot accept a preselected id or node id")
            name = _required_string(arguments.name, "expected release name")
            tag = _required_string(arguments.tag, "expected release tag")
            target = _required_string(arguments.target, "expected release target")
            actual = capture_release(
                arguments.json,
                expected_name=name,
                expected_tag=tag,
                expected_target=target,
            )
            with arguments.github_output.open("a", encoding="utf-8", newline="\n") as output:
                output.write(f"release_id={actual.release_id}\n")
                output.write(f"release_node_id={actual.node_id}\n")
                output.write(f"release_name={actual.name}\n")
                output.write("release_draft=false\n")
                output.write("release_prerelease=false\n")
                output.write("release_immutable=false\n")
        elif any(value is not None for value in identity_values):
            if any(value is None for value in identity_values):
                raise ValueError("full expected release identity is required")
            verify_release(
                arguments.json,
                ReleaseIdentity(
                    release_id=_positive_int(arguments.id, "expected release id"),
                    node_id=_required_string(arguments.node_id, "expected release node id"),
                    name=_required_string(arguments.name, "expected release name"),
                    tag=_required_string(arguments.tag, "expected release tag"),
                    target=_required_string(arguments.target, "expected release target"),
                    draft=False,
                    prerelease=False,
                    immutable=False,
                ),
            )
        else:
            verify_release(arguments.json, RELEASE)
    elif arguments.command == "artifact":
        verify_artifact(
            arguments.json,
            ArtifactIdentity(
                artifact_id=_positive_int(arguments.id, "expected artifact id"),
                node_id=None,
                name=arguments.name,
                sha256=upload_artifact_sha256(arguments.sha256),
                run_id=_positive_int(arguments.run_id, "expected artifact run id"),
                repository_id=_positive_int(
                    arguments.repository_id, "expected artifact repository id"
                ),
                head_repository_id=_positive_int(
                    arguments.repository_id, "expected artifact head repository id"
                ),
                head_sha=arguments.head_sha,
            ),
            require_live=True,
        )
    else:
        verify_pypi_release(
            arguments.json,
            arguments.distributions,
            project="dcc-mcp-wwise",
            version="0.1.4",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
