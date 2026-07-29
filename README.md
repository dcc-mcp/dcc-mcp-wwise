# dcc-mcp-wwise

Typed [DCC-MCP](https://github.com/dcc-mcp/dcc-mcp-core) adapter for
Audiokinetic Wwise Authoring. It connects to Wwise's official loopback WAAPI
endpoint, binds discovery to the concrete Wwise process, and exposes
progressively loaded tools for project inspection, Sound SFX and Music Segment
imports, Events, properties, saves, and bounded audible previews.

## Quick start

Requirements: Wwise 2024.1+ with **Project > User Preferences > Enable Wwise
Authoring API** enabled, Python 3.10+, and `dcc-mcp-cli` 0.19.86+.

```powershell
python -m pip install -e ".[dev]"
$wwisePid = (Get-Process Wwise | Where-Object MainWindowTitle -Like '*your-project*').Id
dcc-mcp-wwise --host-pid $wwisePid
```

Control always follows the typed CLI flow:

```powershell
dcc-mcp-cli list
dcc-mcp-cli search --query "Wwise project info" --dcc-type wwise
dcc-mcp-cli load-skill wwise-project --dcc-type wwise
dcc-mcp-cli describe <returned-tool-slug>
dcc-mcp-cli call <returned-tool-slug> --json '{}'
```

See [install.md](install.md) for setup and
[showcase/audio/README.md](showcase/audio/README.md) for the reproducible audio showcase.

## Runtime shape

```text
dcc-mcp-cli -> DCC-MCP gateway -> dcc-mcp-wwise -> official waapi-client -> Wwise
```

The adapter does not embed Python into Wwise, create another gateway, or expose
arbitrary script execution. The service row is bound to the Wwise host PID, so
it becomes invalid when that authoring instance exits.

## Development

```powershell
vx uv sync --extra dev
vx uv run ruff check src tests tools
vx uv run ruff format --check src tests tools
vx uv run pytest
vx uv run python tools/lint_skills.py
```
