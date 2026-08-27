from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.ci.release_integrity import (
    ArtifactIdentity,
    IncidentIdentity,
    ReleaseIdentity,
    verify_artifact,
    verify_incident,
    verify_pypi_release,
    verify_release,
)


def _artifact_payload(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": 9632474230,
        "node_id": "MDg6QXJ0aWZhY3Q5NjMyNDc0MjMw",
        "name": "python-dist-v0.1.3-33037251075",
        "digest": "sha256:9e28fd0352291399a8499dea12680b2b0b7c56d869e9e1756bdf72a96ca9806c",
        "expired": False,
        "workflow_run": {
            "id": 33037251075,
            "repository_id": 1316365654,
            "head_repository_id": 1316365654,
            "head_sha": "d921113c14ec1c270897b70d553d1261d7a20fa1",
        },
    }
    payload.update(changes)
    return payload


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


def test_original_artifact_live_api_shape_is_fully_bound(tmp_path: Path) -> None:
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(_artifact_payload()), encoding="utf-8")

    verify_artifact(path, ORIGINAL_ARTIFACT, require_live=True)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", 1),
        ("node_id", "other"),
        ("name", "decoy"),
        ("digest", "sha256:" + "0" * 64),
        ("expired", True),
        ("workflow_run", {"id": 1}),
    ],
)
def test_original_artifact_rejects_any_provenance_drift(
    tmp_path: Path, field: str, value: object
) -> None:
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(_artifact_payload(**{field: value})), encoding="utf-8")

    with pytest.raises(ValueError):
        verify_artifact(path, ORIGINAL_ARTIFACT, require_live=True)


def test_incident_run_and_release_live_shapes_are_fully_bound(tmp_path: Path) -> None:
    run = tmp_path / "run.json"
    run.write_text(
        json.dumps(
            {
                "id": 33037251075,
                "node_id": "WFR_kwLOTnYlVs8AAAAHsSxyAw",
                "name": "Release",
                "path": ".github/workflows/release.yml",
                "event": "push",
                "run_attempt": 1,
                "workflow_id": 331601345,
                "head_sha": "d921113c14ec1c270897b70d553d1261d7a20fa1",
                "status": "completed",
                "conclusion": "failure",
                "repository": {
                    "id": 1316365654,
                    "name": "dcc-mcp-wwise",
                    "full_name": "dcc-mcp/dcc-mcp-wwise",
                    "owner": {"login": "dcc-mcp"},
                },
                "head_repository": {
                    "id": 1316365654,
                    "name": "dcc-mcp-wwise",
                    "full_name": "dcc-mcp/dcc-mcp-wwise",
                    "owner": {"login": "dcc-mcp"},
                },
            }
        ),
        encoding="utf-8",
    )
    release = tmp_path / "release.json"
    release.write_text(
        json.dumps(
            {
                "id": 377552005,
                "node_id": "RE_kwDOTnYlVs4WgPyF",
                "tag_name": "v0.1.3",
                "target_commitish": "d921113c14ec1c270897b70d553d1261d7a20fa1",
                "draft": False,
                "prerelease": False,
                "immutable": False,
            }
        ),
        encoding="utf-8",
    )

    verify_incident(
        run,
        IncidentIdentity(
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
        ),
    )
    verify_release(
        release,
        ReleaseIdentity(
            release_id=377552005,
            node_id="RE_kwDOTnYlVs4WgPyF",
            tag="v0.1.3",
            target="d921113c14ec1c270897b70d553d1261d7a20fa1",
            draft=False,
            prerelease=False,
            immutable=False,
        ),
    )


def _pypi_payload(distributions: Path) -> dict[str, object]:
    urls = []
    for path in sorted(distributions.iterdir()):
        wheel = path.suffix == ".whl"
        urls.append(
            {
                "filename": path.name,
                "packagetype": "bdist_wheel" if wheel else "sdist",
                "python_version": "py3" if wheel else "source",
                "size": path.stat().st_size,
                "digests": {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
                "yanked": False,
                "yanked_reason": None,
                "upload_time_iso_8601": "2026-08-27T00:00:00.000000Z",
            }
        )
    return {"info": {"name": "dcc-mcp-wwise", "version": "0.1.3"}, "urls": urls}


def test_existing_pypi_release_requires_exact_healthy_file_metadata(tmp_path: Path) -> None:
    distributions = tmp_path / "dist"
    distributions.mkdir()
    (distributions / "dcc_mcp_wwise-0.1.3-py3-none-any.whl").write_bytes(b"wheel")
    (distributions / "dcc_mcp_wwise-0.1.3.tar.gz").write_bytes(b"sdist")
    metadata = tmp_path / "pypi.json"
    metadata.write_text(json.dumps(_pypi_payload(distributions)), encoding="utf-8")

    verify_pypi_release(metadata, distributions, project="dcc-mcp-wwise", version="0.1.3")


@pytest.mark.parametrize("drift", ["yanked", "yanked_reason", "packagetype", "python_version"])
def test_existing_pypi_release_rejects_yanked_or_type_swapped_files(
    tmp_path: Path, drift: str
) -> None:
    distributions = tmp_path / "dist"
    distributions.mkdir()
    (distributions / "dcc_mcp_wwise-0.1.3-py3-none-any.whl").write_bytes(b"wheel")
    (distributions / "dcc_mcp_wwise-0.1.3.tar.gz").write_bytes(b"sdist")
    payload = _pypi_payload(distributions)
    urls = payload["urls"]
    assert isinstance(urls, list)
    if drift == "yanked":
        urls[0]["yanked"] = True
    elif drift == "yanked_reason":
        urls[0]["yanked_reason"] = "retracted"
    elif drift == "packagetype":
        urls[0]["packagetype"], urls[1]["packagetype"] = (
            urls[1]["packagetype"],
            urls[0]["packagetype"],
        )
    else:
        urls[0]["python_version"] = "source"
    metadata = tmp_path / "pypi.json"
    metadata.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError):
        verify_pypi_release(metadata, distributions, project="dcc-mcp-wwise", version="0.1.3")
