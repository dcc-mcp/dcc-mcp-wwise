"""Public command line for the Wwise adapter."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from .doctor import doctor_report


def _root_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dcc-mcp-wwise",
        description="Verify WAAPI or run the host-bound Wwise adapter service.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=("doctor", "verify"),
        help="run a standalone typed WAAPI verification verb",
    )
    parser.add_argument("--host-pid", metavar="PID", help="bind server mode to Wwise")
    parser.add_argument("--waapi-url", metavar="URL", help="select the typed WAAPI endpoint")
    parser.add_argument("--mcp-port", metavar="PORT", help="select the server listener port")
    return parser


def _doctor_parser(verb: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dcc-mcp-wwise %s" % verb)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--waapi-url")
    parser.add_argument("--host-pid", type=int)
    parser.add_argument("--timeout-ms", type=int, default=5000)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in {"-h", "--help"}:
        _root_parser().parse_args(arguments)
    if not arguments or arguments[0] not in {"doctor", "verify"}:
        from .server import main as server_main

        server_main(arguments)
        return 0

    verb = arguments[0]
    args = _doctor_parser(verb).parse_args(arguments[1:])
    report = doctor_report(
        args.waapi_url,
        verb=verb,
        host_pid=args.host_pid,
        timeout_ms=args.timeout_ms,
    )
    exit_code = int(report.pop("_exit_code"))
    if args.as_json:
        print(json.dumps(report, sort_keys=True))
    else:
        print("%s: %s" % (verb, report["status"]))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
