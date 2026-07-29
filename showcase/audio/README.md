# Wwise audio showcase

These deterministic 48 kHz, 16-bit stereo WAV assets are generated with only
the Python standard library, then imported, organized, adjusted, previewed, and
saved in the bundled Wwise project through `dcc-mcp-wwise` typed tools.

- [UI confirm sound](ui-confirm.wav)
- [Sci-fi impact sound](sci-fi-impact.wav)
- [Neon Circuit background music](neon-circuit-bgm.wav)

| Asset | Duration | Integrated loudness | True peak | SHA-256 |
| --- | ---: | ---: | ---: | --- |
| `ui-confirm.wav` | 0.72 s | -16.9 LUFS | -6.3 dBFS | `4fc3554da6b3bb5cb622afdcee227b44015e07eaea7392f39bd65d5cd688c5b9` |
| `sci-fi-impact.wav` | 1.80 s | -13.5 LUFS | -2.4 dBFS | `da890ab45b3c9821fd4e098d681a8eaafbad4ed80b2ac163070b5e5cfd4335ff` |
| `neon-circuit-bgm.wav` | 12.00 s | -12.2 LUFS | -3.2 dBFS | `f0d55004db0165e2fb62a795cf242b525dcf301ec1e724a8e421bde61a31b417` |

Regenerate them with:

```powershell
python tools/generate_showcase_audio.py
```

The generator is the source and provenance record; the resulting assets are
distributed under the repository's MIT license. Loudness and peak values were
measured with FFmpeg's EBU R128 filter.

The Wwise authoring project used for validation is intentionally kept local.
