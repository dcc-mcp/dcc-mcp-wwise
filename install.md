# Install DCC-MCP Wwise

## Requirements

- Audiokinetic Wwise Authoring 2024.1+.
- WAAPI enabled in **Project > User Preferences**, restricted to loopback
  (`127.0.0.1,::1`) unless an operator deliberately configures a secured remote endpoint.
- Python 3.10+ outside Wwise.
- `dcc-mcp-cli` 0.19.86+ on `PATH`.

## Install and run

```powershell
python -m pip install --upgrade dcc-mcp-wwise
$wwisePid = (Get-Process Wwise | Where-Object MainWindowTitle -Like '*your-project*').Id
dcc-mcp-wwise --host-pid $wwisePid
```

The default WAAPI URL is `ws://127.0.0.1:8080/waapi`. An operator can select a
different endpoint with `--waapi-url` or `DCC_MCP_WWISE_WAAPI_URL`.

## Verify

```powershell
dcc-mcp-cli list
dcc-mcp-cli search --query "Wwise WAAPI ping" --dcc-type wwise
dcc-mcp-cli load-skill wwise-project --dcc-type wwise
dcc-mcp-cli search --query "Wwise WAAPI ping" --dcc-type wwise
dcc-mcp-cli describe <returned-tool-slug>
dcc-mcp-cli call <returned-tool-slug> --json '{}'
```

The running adapter adds a session-scoped **DCC-MCP** menu to Wwise. The menu is
removed when the adapter stops and does not modify Wwise's user configuration.

If multiple Wwise processes are open, pass the PID whose window title contains
the intended project. The adapter never chooses arbitrarily between them.
