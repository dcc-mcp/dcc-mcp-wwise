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

On 2026-07-31, the gameplay variation workflow also completed through typed
tools:

- imported three WAV files into one `ak.wwise.core.object.set` request;
- verified `Footsteps` as a step-mode Random Container with three children;
- assigned `Master Audio Bus` through `ak.wwise.core.object.setReference`;
- created and previewed `Play_Footsteps`;
- generated the Windows `Gameplay` SoundBank with three media items and
  `0 warning(s), 0 error(s), 0 fatal error(s)`;
- verified the disconnected-game runtime profile returns a successful,
  bounded empty snapshot instead of failing.

The generated `Gameplay.bnk`, `ProjectInfo.json`, local Wwise project, adapter
logs, and machine paths are validation artifacts and remain uncommitted.
