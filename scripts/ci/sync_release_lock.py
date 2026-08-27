"""Install one generated release lock from an isolated artifact directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any, Mapping

BARE_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
SERVER_SHA256 = re.compile(r"sha256:([0-9a-f]{64})\Z")
SOURCE_SHA = re.compile(r"[0-9a-f]{40}\Z")
REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _positive_integer(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _argument_integer(value: str, label: str) -> int:
    if not value.isascii() or not value.isdigit() or value.startswith("0"):
        raise ValueError(f"{label} must be a canonical positive integer")
    return _positive_integer(int(value), label)


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _bare_sha256(value: str, label: str) -> str:
    if BARE_SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be exactly 64 lowercase hexadecimal characters")
    return value


def _server_sha256(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("server artifact digest must be a string")
    match = SERVER_SHA256.fullmatch(value)
    if match is None:
        raise ValueError("server artifact digest must use canonical sha256:<64hex> format")
    return match.group(1)


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(getattr(metadata, "st_file_attributes", 0) & REPARSE_POINT)


def _outside_checkout(path: Path, label: str) -> Path:
    absolute = path.absolute()
    checkout = Path.cwd().resolve(strict=True)
    try:
        absolute.relative_to(checkout)
    except ValueError:
        return absolute
    raise ValueError(f"{label} must be outside the checkout")


def prepare(staging: Path) -> None:
    staging = _outside_checkout(staging, "staging directory")
    if os.path.lexists(staging):
        raise ValueError("staging directory must not already exist")
    parent = staging.parent
    metadata = parent.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or _is_reparse(metadata)
    ):
        raise ValueError("staging parent must be a real directory")
    staging.mkdir(mode=0o700)
    if list(os.scandir(staging)):
        raise ValueError("new staging directory must be empty")


def _artifact_bytes(staging: Path) -> bytes:
    staging = _outside_checkout(staging, "staging directory")
    metadata = staging.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or _is_reparse(metadata)
    ):
        raise ValueError("staging directory must be a real directory")
    entries = list(os.scandir(staging))
    if len(entries) != 1 or entries[0].name != "uv.lock":
        raise ValueError("artifact staging must contain exactly one canonical root uv.lock")
    entry = entries[0]
    source = Path(entry.path)
    metadata = source.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or _is_reparse(metadata)
        or metadata.st_nlink != 1
    ):
        raise ValueError("artifact uv.lock must be one unlinked regular file")
    if source.resolve(strict=True).parent != staging.resolve(strict=True):
        raise ValueError("artifact uv.lock must remain inside staging")
    return source.read_bytes()


def _validate_server_identity(
    server_json: Path,
    *,
    artifact_id: int,
    artifact_name: str,
    upload_digest: str,
    run_id: int,
    repository_id: int,
    source_sha: str,
) -> None:
    payload = _mapping(json.loads(server_json.read_text(encoding="utf-8")), "artifact")
    workflow_run = _mapping(payload.get("workflow_run"), "artifact workflow_run")
    if _positive_integer(payload.get("id"), "artifact id") != artifact_id:
        raise ValueError("server artifact id changed")
    if _string(payload.get("name"), "artifact name") != artifact_name:
        raise ValueError("server artifact name changed")
    if payload.get("expired") is not False:
        raise ValueError("server artifact must be live")
    if _server_sha256(payload.get("digest")) != upload_digest:
        raise ValueError("server and upload artifact digests differ")
    if _positive_integer(workflow_run.get("id"), "workflow run id") != run_id:
        raise ValueError("server artifact run changed")
    for field in ("repository_id", "head_repository_id"):
        if _positive_integer(workflow_run.get(field), field) != repository_id:
            raise ValueError("server artifact repository changed")
    if _string(workflow_run.get("head_sha"), "artifact head sha") != source_sha:
        raise ValueError("server artifact source changed")


def install(
    *,
    staging: Path,
    destination: Path,
    server_json: Path,
    artifact_id: str,
    artifact_name: str,
    upload_digest: str,
    lock_digest: str,
    source_sha: str,
    run_id: str,
    repository_id: str,
) -> None:
    upload_digest = _bare_sha256(upload_digest, "upload artifact digest")
    lock_digest = _bare_sha256(lock_digest, "generated lock digest")
    if SOURCE_SHA.fullmatch(source_sha) is None:
        raise ValueError("source sha must be exactly 40 lowercase hexadecimal characters")
    artifact_number = _argument_integer(artifact_id, "artifact id")
    run_number = _argument_integer(run_id, "workflow run id")
    repository_number = _argument_integer(repository_id, "repository id")
    _validate_server_identity(
        server_json,
        artifact_id=artifact_number,
        artifact_name=artifact_name,
        upload_digest=upload_digest,
        run_id=run_number,
        repository_id=repository_number,
        source_sha=source_sha,
    )
    generated = _artifact_bytes(staging)
    if hashlib.sha256(generated).hexdigest() != lock_digest:
        raise ValueError("generated lock digest differs from the credential-free job")

    checkout = Path.cwd().resolve(strict=True)
    destination = destination.absolute()
    if destination.parent.resolve(strict=True) != checkout or destination.name != "uv.lock":
        raise ValueError("destination must be the checkout root uv.lock")
    metadata = destination.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or _is_reparse(metadata)
        or metadata.st_nlink != 1
    ):
        raise ValueError("destination uv.lock must be one unlinked regular file")
    flags = os.O_WRONLY | os.O_TRUNC | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(destination, flags)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(generated)
        stream.flush()
        os.fsync(stream.fileno())
    if destination.read_bytes() != generated:
        raise ValueError("destination uv.lock bytes differ after installation")
    if hashlib.sha256(destination.read_bytes()).hexdigest() != lock_digest:
        raise ValueError("destination uv.lock digest differs after installation")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("--staging", type=Path, required=True)
    install_parser = commands.add_parser("install")
    install_parser.add_argument("--staging", type=Path, required=True)
    install_parser.add_argument("--destination", type=Path, required=True)
    install_parser.add_argument("--server-json", type=Path, required=True)
    install_parser.add_argument("--artifact-id", required=True)
    install_parser.add_argument("--artifact-name", required=True)
    install_parser.add_argument("--upload-digest", required=True)
    install_parser.add_argument("--lock-digest", required=True)
    install_parser.add_argument("--source-sha", required=True)
    install_parser.add_argument("--run-id", required=True)
    install_parser.add_argument("--repository-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "prepare":
            prepare(arguments.staging)
        else:
            install(
                staging=arguments.staging,
                destination=arguments.destination,
                server_json=arguments.server_json,
                artifact_id=arguments.artifact_id,
                artifact_name=arguments.artifact_name,
                upload_digest=arguments.upload_digest,
                lock_digest=arguments.lock_digest,
                source_sha=arguments.source_sha,
                run_id=arguments.run_id,
                repository_id=arguments.repository_id,
            )
    except (OSError, UnicodeError, ValueError) as error:
        print(f"release lock sync failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
