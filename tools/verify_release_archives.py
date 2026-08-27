"""Verify wheel and sdist semantics against the reviewed package source."""

from __future__ import annotations

import ast
import email.parser
import hashlib
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Mapping

from packaging.utils import canonicalize_name


def semantic_digest(path: Path) -> str:
    members: dict[str, bytes] = {}
    if path.name.endswith(".whl"):
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                _safe_name(info.filename)
                if not info.is_dir():
                    if info.filename in members:
                        raise ValueError("archive contains duplicate members")
                    members[info.filename] = archive.read(info)
    elif path.name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            for member in archive.getmembers():
                _safe_name(member.name)
                if member.isfile():
                    if member.name in members:
                        raise ValueError("archive contains duplicate members")
                    file = archive.extractfile(member)
                    if file is None:
                        raise ValueError("archive member is unreadable")
                    members[member.name] = file.read()
    else:
        raise ValueError("unsupported release archive")
    digest = hashlib.sha256()
    for name, data in sorted(members.items()):
        encoded = name.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def _safe_name(name: str) -> PurePosixPath:
    if not name or "\\" in name:
        raise ValueError("archive member name is not portable")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("archive member escapes its archive root")
    return path


def _metadata(data: bytes, *, project: str, version: str) -> None:
    metadata = email.parser.BytesParser().parsebytes(data)
    if canonicalize_name(str(metadata["Name"])) != canonicalize_name(project):
        raise ValueError("archive project name is not canonical")
    if metadata["Version"] != version:
        raise ValueError("archive version does not match")


def _reviewed_content(name: str, data: bytes) -> bytes:
    if PurePosixPath(name).suffix in {".json", ".md", ".py", ".toml", ".txt", ".yaml", ".yml"}:
        return data.replace(b"\r\n", b"\n")
    return data


def _source_files(source: Path) -> dict[str, bytes]:
    return {
        path.relative_to(source).as_posix(): _reviewed_content(
            path.relative_to(source).as_posix(), path.read_bytes()
        )
        for path in source.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }


def _source_version(source: Path) -> str:
    text = (source / "__version__.py").read_text(encoding="utf-8")
    values = [
        node.value.value
        for node in ast.parse(text).body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "__version__"
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ]
    if len(values) != 1 or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", values[0]):
        raise ValueError("source version is not canonical")
    return values[0]


def _wheel_files(path: Path, *, project: str, version: str) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("wheel contains duplicate members")
        for info in archive.infolist():
            _safe_name(info.filename)
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("wheel contains a symbolic link")
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        metadata_roots = {name.split("/", 1)[0] for name in metadata_names}
        if len(metadata_names) != 1 or len(metadata_roots) != 1:
            raise ValueError("wheel must contain one metadata root")
        _metadata(archive.read(metadata_names[0]), project=project, version=version)
        prefix = "dcc_mcp_wwise/"
        return {
            name.removeprefix(prefix): _reviewed_content(
                name.removeprefix(prefix), archive.read(name)
            )
            for name in names
            if name.startswith(prefix) and not name.endswith("/")
        }


def _sdist_files(path: Path, *, project: str, version: str) -> dict[str, bytes]:
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        if len(names) != len(set(names)):
            raise ValueError("sdist contains duplicate members")
        roots: set[str] = set()
        for member in members:
            portable = _safe_name(member.name)
            if portable.parts:
                roots.add(portable.parts[0])
            if not (member.isfile() or member.isdir()):
                raise ValueError("sdist contains a link or special member")
        if len(roots) != 1:
            raise ValueError("sdist must contain one archive root")
        root = next(iter(roots))
        metadata_names = [name for name in names if name == f"{root}/PKG-INFO"]
        if len(metadata_names) != 1:
            raise ValueError("sdist must contain one root metadata record")
        metadata_file = archive.extractfile(metadata_names[0])
        if metadata_file is None:
            raise ValueError("sdist metadata is unreadable")
        _metadata(metadata_file.read(), project=project, version=version)
        prefix = f"{root}/src/dcc_mcp_wwise/"
        result: dict[str, bytes] = {}
        for member in members:
            if not member.isfile() or not member.name.startswith(prefix):
                continue
            file = archive.extractfile(member)
            if file is None:
                raise ValueError("sdist package member is unreadable")
            name = member.name.removeprefix(prefix)
            result[name] = _reviewed_content(name, file.read())
        return result


def _smoke(files: Mapping[str, bytes], *, version: str) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        package = root / "dcc_mcp_wwise"
        for name, data in files.items():
            target = package / Path(*PurePosixPath(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(root)
        subprocess.run(
            [
                sys.executable,
                "-c",
                (f"import dcc_mcp_wwise; assert dcc_mcp_wwise.__version__ == {version!r}"),
            ],
            env=environment,
            check=True,
            capture_output=True,
        )


def _installed_smoke(distribution: Path, *, version: str) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        environment_root = Path(temporary) / "venv"
        subprocess.run(
            [sys.executable, "-m", "venv", str(environment_root)],
            check=True,
            capture_output=True,
        )
        python = environment_root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                str(distribution),
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                str(python),
                "-c",
                (f"import dcc_mcp_wwise; assert dcc_mcp_wwise.__version__ == {version!r}"),
            ],
            check=True,
            capture_output=True,
        )


def verify_pair(
    wheel: Path,
    sdist: Path,
    source: Path,
    project: str,
    version: str,
    wheel_semantic_sha256: str | None = None,
    sdist_semantic_sha256: str | None = None,
) -> None:
    if version == "auto":
        version = _source_version(source)
    expected = _source_files(source)
    wheel_files = _wheel_files(wheel, project=project, version=version)
    sdist_files = _sdist_files(sdist, project=project, version=version)
    if wheel_files != expected:
        raise ValueError("wheel package content differs from the reviewed source tree")
    if sdist_files != expected:
        raise ValueError("sdist package content differs from the reviewed source tree")
    if wheel_semantic_sha256 is not None and semantic_digest(wheel) != wheel_semantic_sha256:
        raise ValueError("wheel semantic digest differs from the frozen incident payload")
    if sdist_semantic_sha256 is not None and semantic_digest(sdist) != sdist_semantic_sha256:
        raise ValueError("sdist semantic digest differs from the frozen incident payload")
    _smoke(expected, version=version)
    _smoke(wheel_files, version=version)
    _smoke(sdist_files, version=version)
    project = source.parents[1]
    if not (project / "pyproject.toml").is_file():
        raise ValueError("reviewed source package root is missing pyproject.toml")
    _installed_smoke(project, version=version)
    _installed_smoke(wheel, version=version)
    _installed_smoke(sdist, version=version)


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) not in {5, 7}:
        raise SystemExit(
            "usage: verify_release_archives.py WHEEL SDIST SOURCE PROJECT VERSION "
            "[WHEEL_SEMANTIC_SHA256 SDIST_SEMANTIC_SHA256]"
        )
    verify_pair(
        Path(arguments[0]),
        Path(arguments[1]),
        Path(arguments[2]),
        arguments[3],
        arguments[4],
        *(arguments[5:7] if len(arguments) == 7 else (None, None)),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
