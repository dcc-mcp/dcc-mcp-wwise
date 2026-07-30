# dcc-mcp-wwise

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/dcc-mcp-wwise-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="docs/assets/dcc-mcp-wwise.svg">
    <img src="docs/assets/dcc-mcp-wwise.svg" alt="DCC-MCP · WWISE" width="600">
  </picture>
</p>

Typed [DCC-MCP](https://github.com/dcc-mcp/dcc-mcp-core) adapter for
Audiokinetic Wwise Authoring. It connects to Wwise's official loopback WAAPI
endpoint, binds discovery to the concrete Wwise process, and exposes
progressively loaded tools for project inspection, Sound SFX and Music Segment
imports, Random/Sequence Containers, Events, properties and references,
connected-game profiler snapshots, SoundBank generation, saves, and bounded
audible previews. While the adapter is connected, Wwise also exposes a
session-scoped **DCC-MCP** menu for the project repository and playable audio
showcase.

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
dcc-mcp-cli call <returned-tool-slug> --json '{}' --wait
```

See [install.md](install.md) for setup and
[showcase/audio/README.md](showcase/audio/README.md) for the reproducible audio showcase.

## Audio showcase

- [▶ Play UI Confirm](https://dcc-mcp.github.io/showcase/wwise#ui-confirm)
- [▶ Play Sci-Fi Impact](https://dcc-mcp.github.io/showcase/wwise#sci-fi-impact)
- [▶ Play Neon Circuit BGM](https://dcc-mcp.github.io/showcase/wwise#neon-circuit-bgm)
- [Footstep variation source WAVs](showcase/audio/README.md#gameplay-footstep-variations)

The footstep example batch-imports three deterministic WAVs into a step-mode
Random Container, assigns its Output Bus, creates and previews
`Play_Footsteps`, then generates a Windows `Gameplay` SoundBank.

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
