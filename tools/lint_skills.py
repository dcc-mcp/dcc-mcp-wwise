from pathlib import Path

from dcc_mcp_core import validate_skill


def main() -> None:
    root = Path(__file__).parents[1] / "src" / "dcc_mcp_wwise" / "skills"
    failed = False
    for skill_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        report = validate_skill(str(skill_dir))
        for issue in report.issues:
            print(f"{skill_dir.name}: [{issue.severity}] {issue.category}: {issue.message}")
            failed |= issue.severity == "error"
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
