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


def _load(path: Path) -> Mapping[str, Any]:
    return _object(json.loads(path.read_text(encoding="utf-8")), path.name)


def verify_artifact(path: Path, expected: ArtifactIdentity, *, require_live: bool) -> None:
    payload = _load(path)
    run = _object(payload.get("workflow_run"), "artifact workflow_run")
    actual = ArtifactIdentity(
        artifact_id=payload.get("id"),
        node_id=payload.get("node_id"),
        name=payload.get("name"),
        sha256=server_artifact_sha256(payload.get("digest")),
        run_id=run.get("id"),
        repository_id=run.get("repository_id"),
        head_repository_id=run.get("head_repository_id"),
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


def _repository_identity(value: object) -> tuple[object, object, object, object]:
    repository = _object(value, "repository")
    owner = _object(repository.get("owner"), "repository owner")
    return (
        repository.get("id"),
        owner.get("login"),
        repository.get("name"),
        repository.get("full_name"),
    )


def verify_incident(path: Path, expected: IncidentIdentity) -> None:
    payload = _load(path)
    actual = IncidentIdentity(
        run_id=payload.get("id"),
        node_id=payload.get("node_id"),
        name=payload.get("name"),
        path=payload.get("path"),
        event=payload.get("event"),
        attempt=payload.get("run_attempt"),
        workflow_id=payload.get("workflow_id"),
        repository_id=_repository_identity(payload.get("repository"))[0],
        repository_owner=_repository_identity(payload.get("repository"))[1],
        repository_name=_repository_identity(payload.get("repository"))[2],
        repository_full_name=_repository_identity(payload.get("repository"))[3],
        head_sha=payload.get("head_sha"),
    )
    if actual != expected:
        raise ValueError("release incident does not match the frozen identity")
    expected_repository = (
        expected.repository_id,
        expected.repository_owner,
        expected.repository_name,
        expected.repository_full_name,
    )
    if _repository_identity(payload.get("head_repository")) != expected_repository:
        raise ValueError("release incident head repository is not the frozen repository")
    if payload.get("status") != "completed" or payload.get("conclusion") != "failure":
        raise ValueError("release incident is not the frozen terminal failure")


def verify_release(path: Path, expected: ReleaseIdentity) -> None:
    payload = _load(path)
    actual = ReleaseIdentity(
        release_id=payload.get("id"),
        node_id=payload.get("node_id"),
        tag=payload.get("tag_name"),
        target=payload.get("target_commitish"),
        draft=payload.get("draft"),
        prerelease=payload.get("prerelease"),
        immutable=payload.get("immutable", False),
    )
    if actual != expected:
        raise ValueError("release does not match the frozen identity")


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


ORIGINAL_ARTIFACT = ArtifactIdentity(
    artifact_id=9632474230,
    node_id="MDg6QXJ0aWZhY3Q5NjMyNDc0MjMw",
    name="python-dist-v0.1.3-33037251075",
    sha256="9e28fd0352291399a8499dea12680b2b0b7c56d869e9e1756bdf72a96ca9806c",
    run_id=33037251075,
    repository_id=1316365654,
    head_repository_id=1316365654,
    head_sha="d921113c14ec1c270897b70d553d1261d7a20fa1",
)
INCIDENT = IncidentIdentity(
    run_id=33037251075,
    node_id="WFR_kwLOTnYlVs8AAAAHsSxyAw",
    name="Release",
    path=".github/workflows/release.yml",
    event="push",
    attempt=1,
    workflow_id=331601345,
    repository_id=1316365654,
    repository_owner="dcc-mcp",
    repository_name="dcc-mcp-wwise",
    repository_full_name="dcc-mcp/dcc-mcp-wwise",
    head_sha="d921113c14ec1c270897b70d553d1261d7a20fa1",
)
RELEASE = ReleaseIdentity(
    release_id=377552005,
    node_id="RE_kwDOTnYlVs4WgPyF",
    tag="v0.1.3",
    target="d921113c14ec1c270897b70d553d1261d7a20fa1",
    draft=False,
    prerelease=False,
    immutable=False,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    original = subparsers.add_parser("original-artifact")
    original.add_argument("json", type=Path)
    original.add_argument("--allow-expired", action="store_true")
    incident = subparsers.add_parser("incident")
    incident.add_argument("json", type=Path)
    release = subparsers.add_parser("release")
    release.add_argument("json", type=Path)
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
    if arguments.command == "original-artifact":
        verify_artifact(arguments.json, ORIGINAL_ARTIFACT, require_live=not arguments.allow_expired)
    elif arguments.command == "incident":
        verify_incident(arguments.json, INCIDENT)
    elif arguments.command == "release":
        verify_release(arguments.json, RELEASE)
    elif arguments.command == "artifact":
        verify_artifact(
            arguments.json,
            ArtifactIdentity(
                artifact_id=arguments.id,
                node_id=None,
                name=arguments.name,
                sha256=upload_artifact_sha256(arguments.sha256),
                run_id=arguments.run_id,
                repository_id=arguments.repository_id,
                head_repository_id=arguments.repository_id,
                head_sha=arguments.head_sha,
            ),
            require_live=True,
        )
    else:
        verify_pypi_release(
            arguments.json,
            arguments.distributions,
            project="dcc-mcp-wwise",
            version="0.1.3",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
