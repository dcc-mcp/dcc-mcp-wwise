"""Fail closed if the adapter wheel carries private compatibility/test files."""

from __future__ import annotations

import email.parser
import re
import sys
import zipfile
from pathlib import Path

from packaging.requirements import Requirement


def _source_version() -> str:
    source = (Path(__file__).parents[1] / "src" / "dcc_mcp_wwise" / "__version__.py").read_text(
        encoding="utf-8"
    )
    match = re.search(r'^__version__ = "([0-9]+\.[0-9]+\.[0-9]+)"', source, re.MULTILINE)
    if match is None:
        raise SystemExit("source version is not canonical")
    return match.group(1)


def verify(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        forbidden = {
            name
            for name in names
            if name.endswith("/install_contract.py") or "/tests/" in f"/{name}"
        }
        if forbidden:
            raise SystemExit("wheel contains compatibility or test files")
        required = {
            "dcc_mcp_wwise/_waapi_probe_worker.py",
            "dcc_mcp_wwise/process_identity.py",
        }
        if not required.issubset(names):
            raise SystemExit("wheel is missing the bounded runtime probe")
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            raise SystemExit("wheel must contain one metadata record")
        metadata = email.parser.BytesParser().parsebytes(archive.read(metadata_names[0]))
        if metadata["Version"] != _source_version():
            raise SystemExit("wheel metadata and source versions differ")
        dependencies = [Requirement(value) for value in metadata.get_all("Requires-Dist", [])]
        core = [requirement for requirement in dependencies if requirement.name == "dcc-mcp-core"]
        if len(core) != 1 or str(core[0].specifier) not in {
            ">=0.20.14,<1.0.0",
            "<1.0.0,>=0.20.14",
        }:
            raise SystemExit("wheel does not require formal dcc-mcp-core 0.20.14")
        waapi = [requirement for requirement in dependencies if requirement.name == "waapi-client"]
        if len(waapi) != 1 or str(waapi[0].specifier) not in {">=0.8.1,<0.9", "<0.9,>=0.8.1"}:
            raise SystemExit("wheel does not bind the audited waapi-client 0.8 line")


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        raise SystemExit("usage: verify_wheel.py WHEEL")
    verify(Path(arguments[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
