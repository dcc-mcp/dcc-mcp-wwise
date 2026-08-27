from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SYNC = ROOT / "scripts" / "ci" / "sync_release_lock.py"


def _run_sync(*arguments: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SYNC), *arguments],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )


def _git(*arguments: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
    )


def _artifact_json(
    path: Path,
    *,
    artifact_id: int,
    artifact_name: str,
    server_digest: str,
    run_id: int,
    repository_id: int,
    source_sha: str,
) -> None:
    path.write_text(
        json.dumps(
            {
                "id": artifact_id,
                "name": artifact_name,
                "digest": server_digest,
                "expired": False,
                "workflow_run": {
                    "id": run_id,
                    "repository_id": repository_id,
                    "head_repository_id": repository_id,
                    "head_sha": source_sha,
                },
            }
        ),
        encoding="utf-8",
    )


def _install_arguments(
    *,
    staging: Path,
    destination: Path,
    server_json: Path,
    lock_digest: str,
    upload_digest: str,
) -> list[str]:
    return [
        "install",
        "--staging",
        str(staging),
        "--destination",
        str(destination),
        "--server-json",
        str(server_json),
        "--artifact-id",
        "1234",
        "--artifact-name",
        "release-lock-13-5678",
        "--upload-digest",
        upload_digest,
        "--lock-digest",
        lock_digest,
        "--source-sha",
        "a" * 40,
        "--run-id",
        "5678",
        "--repository-id",
        "9012",
    ]


def _prepared_install(
    tmp_path: Path,
) -> tuple[Path, Path, Path, Path, bytes, list[str]]:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    subprocess.run(["git", "init"], cwd=checkout, check=True, capture_output=True)
    destination = checkout / "uv.lock"
    destination.write_bytes(b"stale lock\n")
    staging = tmp_path / "downloaded-lock"
    prepared = _run_sync("prepare", "--staging", str(staging), cwd=checkout)
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr
    generated = b'version = 1\nname = "generated-0.1.4"\n'
    (staging / "uv.lock").write_bytes(generated)
    lock_digest = hashlib.sha256(generated).hexdigest()
    upload_digest = "b" * 64
    server_json = tmp_path / "artifact.json"
    _artifact_json(
        server_json,
        artifact_id=1234,
        artifact_name="release-lock-13-5678",
        server_digest=f"sha256:{upload_digest}",
        run_id=5678,
        repository_id=9012,
        source_sha="a" * 40,
    )
    arguments = _install_arguments(
        staging=staging,
        destination=destination,
        server_json=server_json,
        lock_digest=lock_digest,
        upload_digest=upload_digest,
    )
    return checkout, staging, destination, server_json, generated, arguments


def test_prepare_requires_a_fresh_out_of_checkout_directory(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    preexisting = tmp_path / "preexisting"
    preexisting.mkdir()

    existing_result = _run_sync("prepare", "--staging", str(preexisting), cwd=checkout)
    checkout_result = _run_sync("prepare", "--staging", str(checkout / "artifact"), cwd=checkout)

    assert existing_result.returncode == 1
    assert "must not already exist" in existing_result.stderr
    assert checkout_result.returncode == 1
    assert "must be outside the checkout" in checkout_result.stderr
    assert not (checkout / "artifact").exists()


def test_install_rejects_a_staging_directory_symlink(tmp_path: Path) -> None:
    checkout, staging, destination, server_json, generated, _ = _prepared_install(tmp_path)
    real_staging = tmp_path / "real-staging"
    staging.rename(real_staging)
    try:
        staging.symlink_to(real_staging, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlinks unavailable: {error}")
    arguments = _install_arguments(
        staging=staging,
        destination=destination,
        server_json=server_json,
        lock_digest=hashlib.sha256(generated).hexdigest(),
        upload_digest="b" * 64,
    )

    installed = _run_sync(*arguments, cwd=checkout)

    assert installed.returncode == 1
    assert "staging directory must be a real directory" in installed.stderr
    assert destination.read_bytes() == b"stale lock\n"


def test_install_accepts_only_the_exact_download_layout_and_preserves_checkout_metadata(
    tmp_path: Path,
) -> None:
    checkout, _, destination, _, generated, arguments = _prepared_install(tmp_path)
    info_exclude = checkout / ".git" / "info" / "exclude"
    info_exclude.write_text("ignored-dir/\n", encoding="utf-8")
    os.chmod(info_exclude, 0o755)
    exclude_mode = info_exclude.stat().st_mode
    ignored = checkout / "ignored-dir" / "preserved.txt"
    ignored.parent.mkdir()
    ignored.write_bytes(b"preserved\n")

    installed = _run_sync(*arguments, cwd=checkout)

    assert installed.returncode == 0, installed.stdout + installed.stderr
    assert destination.read_bytes() == generated
    assert info_exclude.read_text(encoding="utf-8") == "ignored-dir/\n"
    assert info_exclude.stat().st_mode == exclude_mode
    if os.name != "nt":
        assert exclude_mode & 0o111
    assert ignored.read_bytes() == b"preserved\n"


@pytest.mark.parametrize(
    "relative",
    [
        ".git/info/exclude",
        "ignored-dir/decoy.lock",
        "nested/uv.lock",
        "extra.txt",
    ],
)
def test_install_rejects_every_extra_download_member(tmp_path: Path, relative: str) -> None:
    checkout, staging, destination, _, _, arguments = _prepared_install(tmp_path)
    extra = staging / relative
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_bytes(b"decoy\n")
    os.chmod(extra, 0o755)

    installed = _run_sync(*arguments, cwd=checkout)

    assert installed.returncode == 1
    assert "exactly one canonical root uv.lock" in installed.stderr
    assert destination.read_bytes() == b"stale lock\n"


@pytest.mark.parametrize("link_kind", ["hardlink", "symlink"])
def test_install_rejects_linked_or_path_escaped_lock_files(tmp_path: Path, link_kind: str) -> None:
    checkout, staging, destination, _, _, arguments = _prepared_install(tmp_path)
    (staging / "uv.lock").unlink()
    outside = tmp_path / "outside.lock"
    outside.write_bytes(b'version = 1\nname = "generated-0.1.4"\n')
    try:
        if link_kind == "hardlink":
            os.link(outside, staging / "uv.lock")
        else:
            (staging / "uv.lock").symlink_to(outside)
    except OSError as error:
        pytest.skip(f"{link_kind} unavailable: {error}")

    installed = _run_sync(*arguments, cwd=checkout)

    assert installed.returncode == 1
    assert "one unlinked regular file" in installed.stderr
    assert destination.read_bytes() == b"stale lock\n"


@pytest.mark.parametrize(
    "server_digest",
    [
        "b" * 64,
        "SHA256:" + "b" * 64,
        "sha256:" + "B" * 64,
        "sha256:" + "b" * 64 + " ",
        " sha256:" + "b" * 64,
        "sha512:" + "b" * 64,
    ],
)
def test_install_rejects_noncanonical_server_artifact_digests(
    tmp_path: Path, server_digest: str
) -> None:
    checkout, _, destination, server_json, _, arguments = _prepared_install(tmp_path)
    _artifact_json(
        server_json,
        artifact_id=1234,
        artifact_name="release-lock-13-5678",
        server_digest=server_digest,
        run_id=5678,
        repository_id=9012,
        source_sha="a" * 40,
    )

    installed = _run_sync(*arguments, cwd=checkout)

    assert installed.returncode == 1
    assert "server artifact digest" in installed.stderr
    assert destination.read_bytes() == b"stale lock\n"


@pytest.mark.parametrize(
    "upload_digest",
    [
        "sha256:" + "b" * 64,
        "B" * 64,
        "b" * 64 + " ",
        "b" * 63,
    ],
)
def test_install_rejects_noncanonical_upload_action_digests(
    tmp_path: Path, upload_digest: str
) -> None:
    checkout, staging, destination, server_json, generated, _ = _prepared_install(tmp_path)
    arguments = _install_arguments(
        staging=staging,
        destination=destination,
        server_json=server_json,
        lock_digest=hashlib.sha256(generated).hexdigest(),
        upload_digest=upload_digest,
    )

    installed = _run_sync(*arguments, cwd=checkout)

    assert installed.returncode == 1
    assert "upload artifact digest" in installed.stderr
    assert destination.read_bytes() == b"stale lock\n"


def _lease_repository(tmp_path: Path) -> tuple[Path, Path, str, str, str, str]:
    checkout = tmp_path / "lease-checkout"
    remote = tmp_path / "lease-remote.git"
    checkout.mkdir()
    _git("init", "--initial-branch=main", cwd=checkout)
    _git("config", "user.name", "loonghao", cwd=checkout)
    _git("config", "user.email", "hal.long@outlook.com", cwd=checkout)
    _git("config", "core.autocrlf", "false", cwd=checkout)
    (checkout / "uv.lock").write_bytes(b"base lock\n")
    _git("add", "uv.lock", cwd=checkout)
    _git("commit", "-m", "base", cwd=checkout)
    base = _git("rev-parse", "HEAD", cwd=checkout).stdout.strip()
    (checkout / "source.txt").write_text("source\n", encoding="utf-8")
    _git("add", "source.txt", cwd=checkout)
    _git("commit", "-m", "source", cwd=checkout)
    branch = "release-please--branches--main--components--dcc-mcp-wwise"
    _git("switch", "-c", branch, cwd=checkout)
    expected = _git("rev-parse", "HEAD", cwd=checkout).stdout.strip()
    _git("init", "--bare", str(remote), cwd=tmp_path)
    _git("push", str(remote), f"HEAD:refs/heads/{branch}", cwd=checkout)
    (checkout / "uv.lock").write_bytes(b"generated lock\n")
    _git("add", "uv.lock", cwd=checkout)
    _git("commit", "-m", "sync lock", cwd=checkout)
    candidate = _git("rev-parse", "HEAD", cwd=checkout).stdout.strip()
    return checkout, remote, branch, base, expected, candidate


def _remote_head(remote: Path, branch: str, *, cwd: Path) -> str:
    return _git(
        "--git-dir", str(remote), "rev-parse", f"refs/heads/{branch}", cwd=cwd
    ).stdout.strip()


def _exact_lease_push(
    checkout: Path, remote: Path, branch: str, expected: str
) -> subprocess.CompletedProcess[str]:
    return _git(
        "push",
        f"--force-with-lease=refs/heads/{branch}:{expected}",
        str(remote),
        f"HEAD:refs/heads/{branch}",
        cwd=checkout,
        check=False,
    )


def _drift_remote(
    *,
    tmp_path: Path,
    checkout: Path,
    remote: Path,
    branch: str,
    base: str,
    expected: str,
    candidate: str,
    drift: str,
) -> str:
    if drift == "backward":
        _git(
            "--git-dir",
            str(remote),
            "update-ref",
            f"refs/heads/{branch}",
            base,
            cwd=tmp_path,
        )
        return base

    actor = tmp_path / f"actor-{drift}"
    actor.mkdir()
    _git("init", cwd=actor)
    _git("config", "user.name", "release-bot", cwd=actor)
    _git("config", "user.email", "release-bot@example.invalid", cwd=actor)
    parent = candidate if drift == "forward" else expected
    _git("fetch", str(checkout), parent, cwd=actor)
    _git("checkout", "--detach", parent, cwd=actor)
    (actor / f"{drift}.txt").write_text(f"{drift}\n", encoding="utf-8")
    _git("add", f"{drift}.txt", cwd=actor)
    _git("commit", "-m", drift, cwd=actor)
    drifted = _git("rev-parse", "HEAD", cwd=actor).stdout.strip()
    _git("push", "--force", str(remote), f"HEAD:refs/heads/{branch}", cwd=actor)
    return drifted


def test_exact_source_lease_allows_the_unchanged_remote_head(tmp_path: Path) -> None:
    checkout, remote, branch, _, expected, candidate = _lease_repository(tmp_path)

    pushed = _exact_lease_push(checkout, remote, branch, expected)

    assert pushed.returncode == 0, pushed.stdout + pushed.stderr
    assert _remote_head(remote, branch, cwd=tmp_path) == candidate


@pytest.mark.parametrize("drift", ["backward", "forward", "concurrent"])
def test_exact_source_lease_rejects_every_remote_ref_drift(tmp_path: Path, drift: str) -> None:
    checkout, remote, branch, base, expected, candidate = _lease_repository(tmp_path)
    drifted = _drift_remote(
        tmp_path=tmp_path,
        checkout=checkout,
        remote=remote,
        branch=branch,
        base=base,
        expected=expected,
        candidate=candidate,
        drift=drift,
    )

    pushed = _exact_lease_push(checkout, remote, branch, expected)

    assert pushed.returncode != 0
    assert _remote_head(remote, branch, cwd=tmp_path) == drifted
