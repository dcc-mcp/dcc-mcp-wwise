from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import pytest

import tools.verify_release_archives as release_archives
from tools.verify_release_archives import semantic_digest, verify_pair

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def built_distributions(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    output = tmp_path_factory.mktemp("release-archives")
    subprocess.run(
        [sys.executable, "-m", "build", "--outdir", str(output)],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return next(output.glob("*.whl")), next(output.glob("*.tar.gz"))


def _mutate_sdist(
    source: Path,
    target: Path,
    *,
    remove: str | None = None,
    add: str | None = None,
    replace: str | None = None,
) -> None:
    with tarfile.open(source, "r:gz") as original, tarfile.open(target, "w:gz") as changed:
        for member in original.getmembers():
            if member.name.endswith(remove or "\0"):
                continue
            data = original.extractfile(member).read() if member.isfile() else None
            if member.name.endswith(replace or "\0"):
                data = b"tampered\n"
                member.size = len(data)
            changed.addfile(member, io.BytesIO(data) if data is not None else None)
        if add is not None:
            root = original.getmembers()[0].name.split("/", 1)[0]
            data = b"unexpected\n"
            member = tarfile.TarInfo(f"{root}/src/dcc_mcp_wwise/{add}")
            member.size = len(data)
            member.mode = 0o644
            changed.addfile(member, io.BytesIO(data))


def _copy_reviewed_project(target: Path) -> Path:
    for directory in ("src", "tests", "tools"):
        shutil.copytree(ROOT / directory, target / directory)
    for filename in (
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
    ):
        (target / filename).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / filename, target / filename)
    return target


def _wheel_with_extra_member(source: Path, target: Path) -> None:
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(target, "w") as changed:
        for info in original.infolist():
            changed.writestr(info, original.read(info))
        changed.writestr("reviewer_unvalidated_payload.py", b"REPLACED = True\n")


def _sdist_with_extra_member(source: Path, target: Path) -> None:
    with tarfile.open(source, "r:gz") as original, tarfile.open(target, "w:gz") as changed:
        for member in original.getmembers():
            data = original.extractfile(member).read() if member.isfile() else None
            changed.addfile(member, io.BytesIO(data) if data is not None else None)
        root = original.getmembers()[0].name.split("/", 1)[0]
        data = b"REPLACED = True\n"
        member = tarfile.TarInfo(f"{root}/reviewer_unvalidated_payload.py")
        member.size = len(data)
        member.mode = 0o644
        changed.addfile(member, io.BytesIO(data))


def test_release_archives_match_the_reviewed_source_tree(
    built_distributions: tuple[Path, Path],
) -> None:
    wheel, sdist = built_distributions
    verify_pair(wheel, sdist, ROOT / "src" / "dcc_mcp_wwise", "dcc-mcp-wwise", "auto")


def test_release_archive_verifier_installs_source_wheel_and_sdist(
    monkeypatch: pytest.MonkeyPatch,
    built_distributions: tuple[Path, Path],
) -> None:
    wheel, sdist = built_distributions
    commands: list[list[str]] = []

    def record(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(release_archives.subprocess, "run", record)
    verify_pair(wheel, sdist, ROOT / "src" / "dcc_mcp_wwise", "dcc-mcp-wwise", "auto")

    environments = [command for command in commands if command[1:3] == ["-m", "venv"]]
    assert len(environments) == 3
    assert all("--system-site-packages" not in command for command in environments)
    installs = [command for command in commands if command[1:4] == ["-m", "pip", "install"]]
    installed_urls = [urllib.parse.urlparse(command[-1]) for command in installs]
    assert [Path(url.path).name for url in installed_urls] == [
        "dcc-mcp-wwise-0.1.4-source.zip",
        wheel.name,
        sdist.name,
    ]
    assert all(url.scheme == "http" and url.hostname == "127.0.0.1" for url in installed_urls)
    assert all("--no-deps" not in command for command in installs)


def test_release_archive_verifier_binds_install_inputs_before_cwd_drift(
    monkeypatch: pytest.MonkeyPatch,
    built_distributions: tuple[Path, Path],
) -> None:
    wheel, sdist = built_distributions
    invocation_root = ROOT.parent
    commands: list[list[str]] = []

    def record(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.chdir(invocation_root)
    monkeypatch.setattr(release_archives.subprocess, "run", record)
    verify_pair(
        wheel,
        sdist,
        Path(os.path.relpath(ROOT / "src" / "dcc_mcp_wwise", invocation_root)),
        "dcc-mcp-wwise",
        "auto",
    )

    installs = [command for command in commands if command[1:4] == ["-m", "pip", "install"]]
    installed_urls = [urllib.parse.urlparse(command[-1]) for command in installs]
    assert [Path(url.path).name for url in installed_urls] == [
        "dcc-mcp-wwise-0.1.4-source.zip",
        wheel.name,
        sdist.name,
    ]
    assert all(url.scheme == "http" and url.hostname == "127.0.0.1" for url in installed_urls)


@pytest.mark.parametrize("replaced", ["source", "wheel", "sdist"])
def test_release_archive_transaction_installs_the_bytes_it_validated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    built_distributions: tuple[Path, Path],
    replaced: str,
) -> None:
    built_wheel, built_sdist = built_distributions
    wheel = tmp_path / built_wheel.name
    sdist = tmp_path / built_sdist.name
    shutil.copy2(built_wheel, wheel)
    shutil.copy2(built_sdist, sdist)
    project = _copy_reviewed_project(tmp_path / "tag-source")
    source = project / "src" / "dcc_mcp_wwise"
    original_source = (source / "waapi.py").read_bytes()
    original_wheel = wheel.read_bytes()
    original_sdist = sdist.read_bytes()
    smoke_count = 0

    def replace_after_validation(_: object, *, version: str) -> None:
        del version
        nonlocal smoke_count
        smoke_count += 1
        if smoke_count != 3:
            return
        if replaced == "source":
            (source / "waapi.py").write_bytes(b"REPLACED = True\n")
        elif replaced == "wheel":
            replacement = tmp_path / "replacement.whl"
            _wheel_with_extra_member(wheel, replacement)
            replacement.replace(wheel)
        else:
            replacement = tmp_path / "replacement.tar.gz"
            _sdist_with_extra_member(sdist, replacement)
            replacement.replace(sdist)

    consumed: list[tuple[str, bytes]] = []

    def capture_install(payload: bytes, filename: str, *, version: str) -> None:
        del version
        if filename.endswith("-source.zip"):
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                consumed.append(("source", archive.read("source/src/dcc_mcp_wwise/waapi.py")))
        elif filename.endswith(".whl"):
            consumed.append(("wheel", payload))
        else:
            consumed.append(("sdist", payload))

    monkeypatch.setattr(release_archives, "_smoke", replace_after_validation)
    monkeypatch.setattr(release_archives, "_installed_smoke_bytes", capture_install)

    verify_pair(wheel, sdist, source, "dcc-mcp-wwise", "auto")

    assert consumed == [
        ("source", original_source),
        ("wheel", original_wheel),
        ("sdist", original_sdist),
    ]


def test_release_archive_transaction_exports_only_the_bytes_it_validated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    built_distributions: tuple[Path, Path],
) -> None:
    built_wheel, built_sdist = built_distributions
    wheel = tmp_path / built_wheel.name
    sdist = tmp_path / built_sdist.name
    shutil.copy2(built_wheel, wheel)
    shutil.copy2(built_sdist, sdist)
    original = {wheel.name: wheel.read_bytes(), sdist.name: sdist.read_bytes()}
    smoke_count = 0

    def replace_after_validation(_: object, *, version: str) -> None:
        del version
        nonlocal smoke_count
        smoke_count += 1
        if smoke_count == 3:
            replacement = tmp_path / "replacement.whl"
            _wheel_with_extra_member(wheel, replacement)
            replacement.replace(wheel)

    monkeypatch.setattr(release_archives, "_smoke", replace_after_validation)
    monkeypatch.setattr(release_archives, "_installed_smoke_bytes", lambda *args, **kwargs: None)
    publication = tmp_path / "publication"

    verify_pair(
        wheel,
        sdist,
        ROOT / "src" / "dcc_mcp_wwise",
        "dcc-mcp-wwise",
        "auto",
        snapshot_dir=publication,
    )

    assert {path.name: path.read_bytes() for path in publication.iterdir()} == original


def test_release_archive_transaction_rejects_source_root_movement_during_capture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    built_distributions: tuple[Path, Path],
) -> None:
    wheel, sdist = built_distributions
    project = _copy_reviewed_project(tmp_path / "tag-source")
    retired = tmp_path / "retired-tag-source"
    capture = release_archives._regular_file_bytes
    replaced = False

    def replace_root(path: Path, *, label: str) -> bytes:
        nonlocal replaced
        data = capture(path, label=label)
        if not replaced:
            replaced = True
            project.rename(retired)
            shutil.copytree(retired, project)
        return data

    monkeypatch.setattr(release_archives, "_regular_file_bytes", replace_root)

    with pytest.raises(ValueError, match="project root identity changed"):
        verify_pair(
            wheel,
            sdist,
            project / "src" / "dcc_mcp_wwise",
            "dcc-mcp-wwise",
            "auto",
        )


def test_release_archive_transaction_rejects_package_root_movement_during_capture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    built_distributions: tuple[Path, Path],
) -> None:
    wheel, sdist = built_distributions
    project = _copy_reviewed_project(tmp_path / "tag-source")
    source = project / "src" / "dcc_mcp_wwise"
    retired = project / "src" / "retired_dcc_mcp_wwise"
    capture = release_archives._regular_file_bytes
    replaced = False

    def replace_package_root(path: Path, *, label: str) -> bytes:
        nonlocal replaced
        data = capture(path, label=label)
        if not replaced:
            replaced = True
            source.rename(retired)
            shutil.copytree(retired, source)
        return data

    monkeypatch.setattr(release_archives, "_regular_file_bytes", replace_package_root)

    with pytest.raises(ValueError, match="source package identity changed"):
        verify_pair(wheel, sdist, source, "dcc-mcp-wwise", "auto")


def test_install_smoke_consumes_bound_wheel_bytes_not_a_replaced_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    built_distributions: tuple[Path, Path],
) -> None:
    built_wheel, built_sdist = built_distributions
    wheel = tmp_path / built_wheel.name
    sdist = tmp_path / built_sdist.name
    shutil.copy2(built_wheel, wheel)
    shutil.copy2(built_sdist, sdist)
    expected = wheel.read_bytes()
    consumed: list[bytes] = []

    def consume(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if command[1:4] != ["-m", "pip", "install"]:
            return subprocess.CompletedProcess(command, 0)
        target = command[-1]
        if not target.endswith(".whl"):
            return subprocess.CompletedProcess(command, 0)
        if target.startswith("http://127.0.0.1:"):
            consumed.append(urllib.request.urlopen(target, timeout=5).read())
        else:
            path = Path(target)
            path.replace(path.with_name(path.name + ".captured"))
            path.write_bytes(b"foreign-contender-consumed-by-install")
            consumed.append(path.read_bytes())
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(release_archives.subprocess, "run", consume)
    verify_pair(
        wheel,
        sdist,
        ROOT / "src" / "dcc_mcp_wwise",
        "dcc-mcp-wwise",
        "auto",
    )

    assert consumed == [expected]


def test_snapshot_export_rejects_parent_identity_replacement_before_writing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    built_distributions: tuple[Path, Path],
) -> None:
    wheel, sdist = built_distributions
    publication_root = tmp_path / "publication-root"
    publication_root.mkdir()
    moved_root = tmp_path / "publication-root-captured"
    export = publication_root / "verified-dist"
    directory_identity = release_archives._directory_identity
    swapped = False

    def replace_parent(path: Path, *, label: str) -> tuple[int, int, int, int]:
        nonlocal swapped
        identity = directory_identity(path, label=label)
        if label == "snapshot directory parent" and not swapped:
            swapped = True
            publication_root.rename(moved_root)
            publication_root.mkdir()
        return identity

    monkeypatch.setattr(release_archives, "_directory_identity", replace_parent)
    monkeypatch.setattr(release_archives, "_installed_smoke_bytes", lambda *args, **kwargs: None)

    with pytest.raises(ValueError, match="identity changed|cannot be resolved"):
        verify_pair(
            wheel,
            sdist,
            ROOT / "src" / "dcc_mcp_wwise",
            "dcc-mcp-wwise",
            "auto",
            snapshot_dir=export,
        )
    assert not (publication_root / "verified-dist").exists()


@pytest.mark.skipif(os.name == "nt", reason="snapshot root identity uses POSIX descriptors")
def test_snapshot_export_rejects_root_replacement_during_descriptor_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "snapshot"
    owned_snapshot = tmp_path / "owned-snapshot"
    original_write = release_archives._write_descriptor
    replaced = False

    def replace_root(descriptor: int, data: bytes) -> None:
        nonlocal replaced
        original_write(descriptor, data)
        if not replaced:
            replaced = True
            snapshot.rename(owned_snapshot)
            snapshot.mkdir()

    monkeypatch.setattr(release_archives, "_write_descriptor", replace_root)

    with pytest.raises(ValueError, match="snapshot directory identity changed"):
        release_archives._write_export_snapshot(
            snapshot,
            {"distribution.whl": b"validated", "distribution.tar.gz": b"validated"},
        )
    assert list(snapshot.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="publication bind handoff requires POSIX descriptors")
def test_publication_handoff_mounts_the_validated_directory_object(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "staging"
    expected = {"distribution.whl": b"validated-distribution"}
    consumed: list[bytes] = []

    def consume_descriptor(
        source_descriptor: int,
        target: Path,
        files: dict[str, bytes],
    ) -> None:
        del target
        consumed.append(
            Path(f"/proc/{os.getpid()}/fd/{source_descriptor}/distribution.whl").read_bytes()
        )
        assert files == expected

    monkeypatch.setattr(release_archives, "_mount_readonly_snapshot", consume_descriptor)
    release_archives._write_export_snapshot(
        snapshot,
        expected,
        readonly_bind_dir=tmp_path / "publication",
    )

    assert consumed == [expected["distribution.whl"]]
    assert (snapshot / "distribution.whl").read_bytes() == expected["distribution.whl"]


def test_trusted_manifest_must_name_the_exact_validated_archive_bytes(tmp_path: Path) -> None:
    wheel = b"validated-wheel"
    sdist = b"validated-sdist"
    manifest = tmp_path / "SHA256SUMS"
    expected = (
        f"{release_archives.hashlib.sha256(wheel).hexdigest()} *adapter.whl\n"
        f"{release_archives.hashlib.sha256(sdist).hexdigest()} *adapter.tar.gz\n"
    ).encode("ascii")
    manifest.write_bytes(expected)

    assert (
        release_archives._trusted_manifest(
            manifest,
            "adapter.whl",
            wheel,
            "adapter.tar.gz",
            sdist,
        )
        == expected
    )

    manifest.write_bytes(b"0" + expected[1:])
    with pytest.raises(ValueError, match="differs from the validated archives"):
        release_archives._trusted_manifest(
            manifest,
            "adapter.whl",
            wheel,
            "adapter.tar.gz",
            sdist,
        )


def test_release_archive_transaction_refuses_an_existing_publication_directory(
    tmp_path: Path,
    built_distributions: tuple[Path, Path],
) -> None:
    wheel, sdist = built_distributions
    publication = tmp_path / "publication"
    publication.mkdir()

    with pytest.raises(ValueError, match="snapshot directory must not already exist"):
        verify_pair(
            wheel,
            sdist,
            ROOT / "src" / "dcc_mcp_wwise",
            "dcc-mcp-wwise",
            "auto",
            snapshot_dir=publication,
        )


@pytest.mark.parametrize("archive_kind", ["wheel", "sdist"])
def test_release_archive_transaction_rejects_hardlinked_inputs(
    tmp_path: Path,
    built_distributions: tuple[Path, Path],
    archive_kind: str,
) -> None:
    wheel, sdist = built_distributions
    private_wheel = tmp_path / "input" / wheel.name
    private_sdist = tmp_path / "input" / sdist.name
    private_wheel.parent.mkdir()
    shutil.copy2(wheel, private_wheel)
    shutil.copy2(sdist, private_sdist)
    linked_wheel = private_wheel
    linked_sdist = private_sdist
    if archive_kind == "wheel":
        linked_wheel = tmp_path / wheel.name
        os.link(private_wheel, linked_wheel)
    else:
        linked_sdist = tmp_path / sdist.name
        os.link(private_sdist, linked_sdist)

    with pytest.raises(ValueError, match="must not have hard links"):
        verify_pair(
            linked_wheel,
            linked_sdist,
            ROOT / "src" / "dcc_mcp_wwise",
            "dcc-mcp-wwise",
            "auto",
        )


@pytest.mark.parametrize("archive_kind", ["wheel", "sdist"])
def test_release_archives_reject_every_unapproved_member(
    tmp_path: Path,
    built_distributions: tuple[Path, Path],
    archive_kind: str,
) -> None:
    wheel, sdist = built_distributions
    changed_wheel = wheel
    changed_sdist = sdist
    if archive_kind == "wheel":
        changed_wheel = tmp_path / wheel.name
        _wheel_with_extra_member(wheel, changed_wheel)
    else:
        changed_sdist = tmp_path / sdist.name
        _sdist_with_extra_member(sdist, changed_sdist)

    with pytest.raises(ValueError, match="unapproved archive member"):
        verify_pair(
            changed_wheel,
            changed_sdist,
            ROOT / "src" / "dcc_mcp_wwise",
            "dcc-mcp-wwise",
            "auto",
        )


@pytest.mark.parametrize(
    ("returncode", "expected_exit"),
    [(1, "1"), (10**1000, "unknown")],
)
def test_installed_smoke_reports_bounded_redacted_pip_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    returncode: int,
    expected_exit: str,
) -> None:
    distribution = tmp_path / "tag-source"
    distribution.mkdir()
    secret = "credential=do-not-print"
    sensitive_path = str(tmp_path / "private" / "source")

    def fail_install(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if command[1:4] == ["-m", "pip", "install"]:
            raise subprocess.CalledProcessError(
                returncode,
                command,
                output=f"download URL contains {secret}",
                stderr=(
                    "ERROR: Could not find a version that satisfies the requirement "
                    f"tag-source from {sensitive_path}\n"
                    "ERROR: No matching distribution found for tag-source"
                ),
            )
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(release_archives.subprocess, "run", fail_install)

    with pytest.raises(RuntimeError) as failure:
        release_archives._installed_smoke(distribution, version="0.1.4")

    message = str(failure.value)
    assert message == (
        "installed smoke pip install failed "
        f"(exit={expected_exit}; reason=no-matching-distribution)"
    )
    assert secret not in message
    assert sensitive_path not in message
    assert len(message) < 120


def test_wheel_semantic_digest_ignores_container_recompression(
    tmp_path: Path, built_distributions: tuple[Path, Path]
) -> None:
    wheel, _ = built_distributions
    repacked = tmp_path / wheel.name
    with (
        zipfile.ZipFile(wheel) as original,
        zipfile.ZipFile(repacked, "w", compression=zipfile.ZIP_STORED) as changed,
    ):
        for name in reversed(original.namelist()):
            changed.writestr(name, original.read(name))

    assert wheel.read_bytes() != repacked.read_bytes()
    assert semantic_digest(wheel) == semantic_digest(repacked)


def test_release_content_comparison_normalizes_portable_text_line_endings(
    tmp_path: Path,
) -> None:
    lf = tmp_path / "lf"
    crlf = tmp_path / "crlf"
    lf.mkdir()
    crlf.mkdir()
    (lf / "module.py").write_bytes(b"value = 1\n")
    (crlf / "module.py").write_bytes(b"value = 1\r\n")

    assert release_archives._source_files(lf) == release_archives._source_files(crlf)


@pytest.mark.parametrize(
    ("remove", "add", "replace"),
    [
        ("src/dcc_mcp_wwise/waapi.py", None, None),
        (None, "private.py", None),
        (None, None, "src/dcc_mcp_wwise/skills/wwise-project/tools.yaml"),
    ],
)
def test_release_archives_reject_missing_extra_or_changed_sdist_runtime_content(
    tmp_path: Path,
    built_distributions: tuple[Path, Path],
    remove: str | None,
    add: str | None,
    replace: str | None,
) -> None:
    wheel, sdist = built_distributions
    changed = tmp_path / sdist.name
    _mutate_sdist(sdist, changed, remove=remove, add=add, replace=replace)

    with pytest.raises(ValueError):
        verify_pair(wheel, changed, ROOT / "src" / "dcc_mcp_wwise", "dcc-mcp-wwise", "auto")
