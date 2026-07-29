from pathlib import Path

from dcc_mcp_core import validate_skill


def test_bundled_skills_validate():
    root = Path(__file__).parents[1] / "src" / "dcc_mcp_wwise" / "skills"
    for skill_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        report = validate_skill(str(skill_dir))
        errors = [issue.message for issue in report.issues if issue.severity == "error"]
        assert not errors, f"{skill_dir.name}: {errors}"
