"""Verify wheel and sdist semantics against the reviewed package source."""

from __future__ import annotations

import argparse
import ast
import email.parser
import hashlib
import os
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Mapping

from packaging.utils import canonicalize_name

PROJECT_ROOT_FILES = (
    ".gitignore",
    ".release-please-manifest.json",
    "CHANGELOG.md",
    "install.md",
    "LICENSE",
    "pyproject.toml",
    "README.md",
    "release-please-config.json",
    "showcase/audio/README.md",
    "showcase/evidence/README.md",
)
PROJECT_TREES = ("src/dcc_mcp_wwise", "tests", "tools")


def _canonical_existing(path: Path, *, label: str) -> Path:
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            if _is_link_or_reparse(current):
                raise ValueError(f"{label} path must not contain a link or reparse point")
        except OSError:
            break
    try:
        return absolute.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValueError(f"{label} is missing or cannot be resolved canonically") from None


def _regular_file_bytes(path: Path, *, label: str) -> bytes:
    try:
        file_stat = path.lstat()
    except OSError as error:
        raise ValueError(f"{label} cannot be inspected: {error.__class__.__name__}") from None
    file_attributes = getattr(file_stat, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if path.is_symlink() or (reparse_flag and file_attributes & reparse_flag):
        raise ValueError(f"{label} must not be a link or reparse point")
    if not stat.S_ISREG(file_stat.st_mode):
        raise ValueError(f"{label} must be a regular file")
    if file_stat.st_nlink != 1:
        raise ValueError(f"{label} must not have hard links")
    identity = (
        file_stat.st_dev,
        file_stat.st_ino,
        file_stat.st_size,
        file_stat.st_mtime_ns,
        stat.S_IFMT(file_stat.st_mode),
        file_attributes,
    )
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as source:
            opened_stat = os.fstat(source.fileno())
            data = source.read()
            completed_stat = os.fstat(source.fileno())
    except OSError as error:
        raise ValueError(f"{label} cannot be read: {error.__class__.__name__}") from None
    for observed in (opened_stat, completed_stat):
        observed_identity = (
            observed.st_dev,
            observed.st_ino,
            observed.st_size,
            observed.st_mtime_ns,
            stat.S_IFMT(observed.st_mode),
            getattr(observed, "st_file_attributes", 0),
        )
        if (
            observed_identity != identity
            or not stat.S_ISREG(observed.st_mode)
            or observed.st_nlink != 1
        ):
            raise ValueError(f"{label} identity changed while it was captured")
    try:
        final_stat = path.lstat()
    except OSError:
        raise ValueError(f"{label} identity changed while it was captured") from None
    final_identity = (
        final_stat.st_dev,
        final_stat.st_ino,
        final_stat.st_size,
        final_stat.st_mtime_ns,
        stat.S_IFMT(final_stat.st_mode),
        getattr(final_stat, "st_file_attributes", 0),
    )
    if final_identity != identity or final_stat.st_nlink != 1 or _is_link_or_reparse(path):
        raise ValueError(f"{label} identity changed while it was captured")
    return data


def _is_link_or_reparse(path: Path) -> bool:
    file_stat = path.lstat()
    file_attributes = getattr(file_stat, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return path.is_symlink() or bool(reparse_flag and file_attributes & reparse_flag)


def _directory_identity(path: Path, *, label: str) -> tuple[int, int, int, int]:
    try:
        directory_stat = path.lstat()
    except OSError as error:
        raise ValueError(f"{label} cannot be inspected: {error.__class__.__name__}") from None
    if _is_link_or_reparse(path) or not stat.S_ISDIR(directory_stat.st_mode):
        raise ValueError(f"{label} must be a regular directory")
    return (
        directory_stat.st_dev,
        directory_stat.st_ino,
        stat.S_IFMT(directory_stat.st_mode),
        getattr(directory_stat, "st_file_attributes", 0),
    )


def _project_files(project: Path) -> dict[str, bytes]:
    project_identity = _directory_identity(project, label="project root")
    result: dict[str, bytes] = {}
    for relative in PROJECT_ROOT_FILES:
        result[relative] = _regular_file_bytes(project / relative, label=relative)
    for relative in PROJECT_TREES:
        tree = project / Path(*PurePosixPath(relative).parts)
        if not tree.is_dir() or _is_link_or_reparse(tree):
            raise ValueError(f"{relative} must be a regular project directory")
        for path in tree.rglob("*"):
            if _is_link_or_reparse(path):
                raise ValueError(f"{path.relative_to(project).as_posix()} must not be a link")
            if path.is_dir() or "__pycache__" in path.parts:
                continue
            name = path.relative_to(project).as_posix()
            result[name] = _regular_file_bytes(path, label=name)
    if _directory_identity(project, label="project root") != project_identity:
        raise ValueError("project root identity changed while it was captured")
    return result


def _write_private_snapshot(root: Path, files: Mapping[str, bytes]) -> None:
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name, data in files.items():
        portable = _safe_name(name)
        target = root.joinpath(*portable.parts)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with target.open("xb") as output:
            output.write(data)


def _verify_private_file(path: Path, expected: bytes, *, label: str) -> Path:
    if _regular_file_bytes(path, label=label) != expected:
        raise ValueError(f"{label} bytes changed after capture")
    return path


def _verify_private_project(path: Path, expected: Mapping[str, bytes]) -> Path:
    if _project_files(path) != expected:
        raise ValueError("private project bytes changed after capture")
    return path


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


def _wheel_files(
    path: Path,
    *,
    project: str,
    version: str,
    expected: Mapping[str, bytes],
    project_files: Mapping[str, bytes],
) -> dict[str, bytes]:
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
        metadata_root = next(iter(metadata_roots))
        expected_metadata_root = (
            f"{canonicalize_name(project).replace('-', '_')}-{version}.dist-info"
        )
        if metadata_root != expected_metadata_root:
            raise ValueError("wheel metadata root is not canonical")
        package_prefix = "dcc_mcp_wwise/"
        approved = {f"{package_prefix}{name}" for name in expected}
        approved.update(
            f"{metadata_root}/{name}"
            for name in (
                "METADATA",
                "WHEEL",
                "entry_points.txt",
                "licenses/LICENSE",
                "RECORD",
            )
        )
        if set(names) != approved:
            raise ValueError("wheel contains an unapproved archive member")
        _metadata(archive.read(metadata_names[0]), project=project, version=version)
        if _reviewed_content("LICENSE", archive.read(f"{metadata_root}/licenses/LICENSE")) != (
            _reviewed_content("LICENSE", project_files["LICENSE"])
        ):
            raise ValueError("wheel license differs from the reviewed project")
        return {
            name.removeprefix(package_prefix): _reviewed_content(
                name.removeprefix(package_prefix), archive.read(name)
            )
            for name in names
            if name.startswith(package_prefix)
        }


def _sdist_files(
    path: Path,
    *,
    project: str,
    version: str,
    project_files: Mapping[str, bytes],
) -> dict[str, bytes]:
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
            if not member.isfile():
                raise ValueError("sdist contains an unapproved archive member")
        if len(roots) != 1:
            raise ValueError("sdist must contain one archive root")
        root = next(iter(roots))
        approved = {f"{root}/{name}" for name in project_files}
        approved.add(f"{root}/PKG-INFO")
        if set(names) != approved:
            raise ValueError("sdist contains an unapproved archive member")
        metadata_names = [name for name in names if name == f"{root}/PKG-INFO"]
        if len(metadata_names) != 1:
            raise ValueError("sdist must contain one root metadata record")
        metadata_file = archive.extractfile(metadata_names[0])
        if metadata_file is None:
            raise ValueError("sdist metadata is unreadable")
        _metadata(metadata_file.read(), project=project, version=version)
        for name, expected_data in project_files.items():
            member = archive.extractfile(f"{root}/{name}")
            if member is None:
                raise ValueError("sdist project member is unreadable")
            if _reviewed_content(name, member.read()) != _reviewed_content(name, expected_data):
                raise ValueError("sdist project content differs from the reviewed source tree")
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


def _pip_failure_reason(error: subprocess.CalledProcessError) -> str:
    diagnostic = "\n".join(
        value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
        for value in (error.stdout, error.stderr)
        if isinstance(value, (str, bytes))
    ).lower()
    if "no matching distribution found" in diagnostic:
        return "no-matching-distribution"
    if "could not find a version that satisfies the requirement" in diagnostic:
        return "dependency-resolution"
    if "invalid requirement" in diagnostic:
        return "invalid-requirement"
    if "does not appear to be a python project" in diagnostic:
        return "invalid-python-project"
    if "no such file or directory" in diagnostic or "the system cannot find" in diagnostic:
        return "input-not-found"
    return "unclassified"


def _installed_smoke(distribution: Path, *, version: str) -> None:
    distribution = _canonical_existing(distribution, label="installed smoke input")
    with tempfile.TemporaryDirectory() as temporary:
        environment_root = Path(temporary) / "venv"
        subprocess.run(
            [sys.executable, "-m", "venv", str(environment_root)],
            check=True,
            capture_output=True,
        )
        python = environment_root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        try:
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
        except subprocess.CalledProcessError as error:
            exit_code = (
                error.returncode
                if type(error.returncode) is int and 0 <= error.returncode <= 0xFFFFFFFF
                else "unknown"
            )
            reason = _pip_failure_reason(error)
            raise RuntimeError(
                f"installed smoke pip install failed (exit={exit_code}; reason={reason})"
            ) from None
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
    *,
    snapshot_dir: Path | None = None,
) -> None:
    wheel = _canonical_existing(wheel, label="wheel")
    sdist = _canonical_existing(sdist, label="sdist")
    source = _canonical_existing(source, label="reviewed source package")
    source_project = source.parents[1]
    if snapshot_dir is not None and snapshot_dir.exists():
        raise ValueError("snapshot directory must not already exist")
    source_identity = _directory_identity(source, label="source package")
    project_snapshot = _project_files(source_project)
    if _directory_identity(source, label="source package") != source_identity:
        raise ValueError("source package identity changed while it was captured")
    wheel_snapshot = _regular_file_bytes(wheel, label="wheel")
    sdist_snapshot = _regular_file_bytes(sdist, label="sdist")
    with tempfile.TemporaryDirectory(prefix="dcc-mcp-wwise-release-") as temporary:
        transaction = Path(temporary)
        private_project = transaction / "source"
        private_archives = transaction / "archives"
        _write_private_snapshot(private_project, project_snapshot)
        _write_private_snapshot(
            private_archives,
            {wheel.name: wheel_snapshot, sdist.name: sdist_snapshot},
        )
        private_source = private_project / "src" / "dcc_mcp_wwise"
        private_wheel = private_archives / wheel.name
        private_sdist = private_archives / sdist.name
        _verify_private_project(private_project, project_snapshot)
        _verify_private_file(private_wheel, wheel_snapshot, label="private wheel")
        _verify_private_file(private_sdist, sdist_snapshot, label="private sdist")
        if version == "auto":
            version = _source_version(private_source)
        expected = _source_files(private_source)
        wheel_files = _wheel_files(
            _verify_private_file(private_wheel, wheel_snapshot, label="private wheel"),
            project=project,
            version=version,
            expected=expected,
            project_files=project_snapshot,
        )
        sdist_files = _sdist_files(
            _verify_private_file(private_sdist, sdist_snapshot, label="private sdist"),
            project=project,
            version=version,
            project_files=project_snapshot,
        )
        if wheel_files != expected:
            raise ValueError("wheel package content differs from the reviewed source tree")
        if sdist_files != expected:
            raise ValueError("sdist package content differs from the reviewed source tree")
        if (
            wheel_semantic_sha256 is not None
            and semantic_digest(
                _verify_private_file(private_wheel, wheel_snapshot, label="private wheel")
            )
            != wheel_semantic_sha256
        ):
            raise ValueError("wheel semantic digest differs from the frozen incident payload")
        if (
            sdist_semantic_sha256 is not None
            and semantic_digest(
                _verify_private_file(private_sdist, sdist_snapshot, label="private sdist")
            )
            != sdist_semantic_sha256
        ):
            raise ValueError("sdist semantic digest differs from the frozen incident payload")
        _smoke(expected, version=version)
        _smoke(wheel_files, version=version)
        _smoke(sdist_files, version=version)
        install_project = transaction / "install" / "source"
        install_archives = transaction / "install" / "archives"
        _write_private_snapshot(install_project, project_snapshot)
        _write_private_snapshot(
            install_archives,
            {wheel.name: wheel_snapshot, sdist.name: sdist_snapshot},
        )
        install_wheel = install_archives / wheel.name
        install_sdist = install_archives / sdist.name
        _installed_smoke(
            _verify_private_project(install_project, project_snapshot), version=version
        )
        _verify_private_project(install_project, project_snapshot)
        _installed_smoke(
            _verify_private_file(install_wheel, wheel_snapshot, label="install wheel"),
            version=version,
        )
        _verify_private_file(install_wheel, wheel_snapshot, label="install wheel")
        _installed_smoke(
            _verify_private_file(install_sdist, sdist_snapshot, label="install sdist"),
            version=version,
        )
        _verify_private_file(install_sdist, sdist_snapshot, label="install sdist")
        if snapshot_dir is not None:
            try:
                snapshot_parent = _canonical_existing(
                    snapshot_dir.parent, label="snapshot directory parent"
                )
                _directory_identity(snapshot_parent, label="snapshot directory parent")
            except (OSError, ValueError):
                raise ValueError("snapshot directory parent cannot be resolved") from None
            _write_private_snapshot(
                snapshot_parent / snapshot_dir.name,
                {wheel.name: wheel_snapshot, sdist.name: sdist_snapshot},
            )
            _verify_private_file(
                snapshot_parent / snapshot_dir.name / wheel.name,
                wheel_snapshot,
                label="published wheel snapshot",
            )
            _verify_private_file(
                snapshot_parent / snapshot_dir.name / sdist.name,
                sdist_snapshot,
                label="published sdist snapshot",
            )


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel")
    parser.add_argument("sdist")
    parser.add_argument("source")
    parser.add_argument("project")
    parser.add_argument("version")
    parser.add_argument("semantic_digests", nargs="*")
    parser.add_argument("--snapshot-dir", type=Path)
    parsed = parser.parse_args(arguments)
    if len(parsed.semantic_digests) not in {0, 2}:
        parser.error("semantic digests must include both wheel and sdist SHA-256 values")
    verify_pair(
        Path(parsed.wheel),
        Path(parsed.sdist),
        Path(parsed.source),
        parsed.project,
        parsed.version,
        *(parsed.semantic_digests if parsed.semantic_digests else (None, None)),
        snapshot_dir=parsed.snapshot_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
