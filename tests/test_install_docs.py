from pathlib import Path


def test_install_runbook_is_wheel_first_platform_complete_and_honest_about_publication():
    root = Path(__file__).parents[1]
    text = (root / "install.md").read_text(encoding="utf-8")

    for heading in (
        "## Requirements",
        "## Supported versions",
        "## Agent quick path",
        "## Manual path",
        "## Verify",
        "## Upgrade",
        "## Uninstall",
        "## Troubleshooting",
    ):
        assert heading in text
    for platform in ("Windows", "macOS", "Linux"):
        assert platform in text
    for command in (
        "python -m pip install --upgrade dcc-mcp-wwise",
        "dcc-mcp-wwise doctor --json",
        "dcc-mcp-wwise verify --json",
        "python -m pip uninstall dcc-mcp-wwise",
    ):
        assert command in text
    assert "https://raw.githubusercontent.com/dcc-mcp/dcc-mcp-wwise/main/install.md" in text
    assert "DCC_MCP_WWISE_WAAPI_ALLOWED_HOSTS" in text
    assert "PyPI wheel is not published" in text
    assert "no adapter-managed external binary cache" in text
    assert "Linux remote verification is preflight-only" in text
    assert "Remote WSS success returns exit `10` with `failure_stage: host_binding`" in text
    assert "dcc-mcp-core>=0.20.14,<1.0.0" in text
    assert "Loopback success without `--host-pid` also returns exit `10`" in text
    assert "dcc-mcp/dcc-mcp-core#2252" in text
    assert 'pip install -e ".[dev]"' not in text


def test_readme_quick_start_routes_agents_to_doctor_without_claiming_a_published_wheel():
    readme = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")
    quick_start = readme.split("## Quick start", 1)[1].split("## Audio showcase", 1)[0]

    assert "PyPI wheel is not published" in quick_start
    assert "dcc-mcp-wwise doctor --json" in quick_start
    assert "dcc-mcp-wwise verify --json" in quick_start
    assert "--host-pid $wwisePid" in quick_start
    assert 'pip install -e ".[dev]"' not in quick_start
    assert "install.md" in quick_start


def test_ci_runs_the_public_doctor_contract_smoke():
    workflow = (Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert "Doctor contract smoke" in workflow
    assert "pytest tests/test_doctor.py -q" in workflow


def test_ci_smokes_the_built_wheel_entry_point():
    workflow = (Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert "Wheel entry-point smoke" in workflow
    assert "wheel-smoke/bin/dcc-mcp-wwise --version" in workflow
