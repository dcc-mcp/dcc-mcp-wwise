# Live Wwise validation

Validated on 2026-07-30 against a local Wwise Authoring `v2024.1.1` instance.

The adapter completed the required typed flow through `dcc-mcp-cli`:
`list` → `search` → `load-skill` → `describe` → `call --wait`.

Final WAAPI queries returned:

| Wwise object | Type | Duration | Volume |
| --- | --- | ---: | ---: |
| `UI Confirm` | Sound | 0.72 s | -2 dB |
| `Sci Fi Impact` | Sound | 1.80 s | -1 dB |
| `Neon Circuit BGM` | MusicSegment | 12.00 s | -5 dB |

Events `Play_UI_Confirm`, `Play_Sci_Fi_Impact`, and
`Play_Neon_Circuit_BGM` were created. All three preview jobs and the local
project save completed.
