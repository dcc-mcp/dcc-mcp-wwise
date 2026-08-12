---
name: wwise-audio
description: Author and preview typed Wwise game audio through WAAPI, including SFX and music Switch or State containers, RTPC curves, imports, Events, properties, and references. Use after wwise-project confirms the intended project.
license: MIT
compatibility: "Python 3.10+, Wwise 2024.1+"
metadata:
  dcc-mcp:
    dcc: wwise
    layer: domain
    version: "0.1.1"  # x-release-please-version
    tags: [wwise, waapi, audio, sound-design, music]
    search-hint: "Wwise RPG game audio design Switch State group container assignment RTPC curve Game Parameter dynamic music adaptive combat exploration SFX import variation Event output bus attenuation preview"
    tools: tools.yaml
---

# Wwise Audio

Use these typed tools for normal Wwise authoring. Confirm the active project
with `wwise-project` first. Imports accept existing local WAV files and create
named Wwise objects or variation containers under bounded default Work Unit
folders. Preview is bounded; its short-lived WAAPI connection owns and releases
the transport. For an end-to-end RPG workflow and acceptance checklist, read
`references/RPG_GAME_AUDIO_WORKFLOW.md`.
