from __future__ import annotations

import io
import os
import subprocess
import sys
import tarfile
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
    assert [Path(command[-1]) for command in installs] == [ROOT, wheel, sdist]
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
    assert [Path(command[-1]) for command in installs] == [
        ROOT.resolve(),
        wheel.resolve(),
        sdist.resolve(),
    ]


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
