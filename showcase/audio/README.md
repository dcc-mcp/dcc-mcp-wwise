# Wwise audio showcase

These deterministic 48 kHz, 16-bit stereo WAV assets are generated with only
the Python standard library, then imported, organized, adjusted, previewed, and
saved in a local validation project through `dcc-mcp-wwise` typed tools.

- [▶ Play UI Confirm](https://dcc-mcp.github.io/showcase/wwise#ui-confirm) · [WAV](ui-confirm.wav)
- [▶ Play Sci-Fi Impact](https://dcc-mcp.github.io/showcase/wwise#sci-fi-impact) · [WAV](sci-fi-impact.wav)
- [▶ Play Neon Circuit BGM](https://dcc-mcp.github.io/showcase/wwise#neon-circuit-bgm) · [WAV](neon-circuit-bgm.wav)

| Asset | Duration | Integrated loudness | True peak | SHA-256 |
| --- | ---: | ---: | ---: | --- |
| `ui-confirm.wav` | 0.72 s | -16.9 LUFS | -6.3 dBFS | `4fc3554da6b3bb5cb622afdcee227b44015e07eaea7392f39bd65d5cd688c5b9` |
| `sci-fi-impact.wav` | 1.80 s | -13.5 LUFS | -2.4 dBFS | `da890ab45b3c9821fd4e098d681a8eaafbad4ed80b2ac163070b5e5cfd4335ff` |
| `neon-circuit-bgm.wav` | 12.00 s | -12.2 LUFS | -3.2 dBFS | `f0d55004db0165e2fb62a795cf242b525dcf301ec1e724a8e421bde61a31b417` |

## Gameplay footstep variations

The three source takes below were batch-imported into
`Gameplay\Footsteps`, a step-mode Random Container routed to the Master Audio
Bus. The `Play_Footsteps` Event was previewed successfully, and the Windows
`Gameplay` SoundBank was generated with three media items and no warnings or
errors.

| Source take | Duration | True peak | SHA-256 |
| --- | ---: | ---: | --- |
| [footstep-01.wav](footstep-01.wav) | 0.34 s | -2.1 dBFS | `8b473e299fc70d5aeae67eb2c099f8f0dcd21f32e9a01348478db1f2a408293d` |
| [footstep-02.wav](footstep-02.wav) | 0.37 s | -2.1 dBFS | `8089c69abcc65364ea8c36b916928858ea5d10afe8208cbb5b0cd10d4ebf853c` |
| [footstep-03.wav](footstep-03.wav) | 0.32 s | -1.7 dBFS | `56c9dd38d03fde42027e60dcd40f3091e6908a12a66b8e58ee684899d699921e` |

Regenerate them with:

```powershell
python tools/generate_showcase_audio.py
```

The generator is the source and provenance record; the resulting assets are
distributed under the repository's MIT license. Loudness and true-peak values
were measured with FFmpeg's EBU R128 filter.

The Wwise authoring project used for validation is intentionally kept local.
