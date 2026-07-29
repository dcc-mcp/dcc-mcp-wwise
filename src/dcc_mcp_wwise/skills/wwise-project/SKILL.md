---
name: wwise-project
description: Inspect, query, validate, generate SoundBanks, and save the active Audiokinetic Wwise project through WAAPI. Use for project and Wwise UI state; use wwise-audio for authoring audio objects and previews.
license: MIT
compatibility: "Python 3.10+, Wwise 2024.1+"
metadata:
  dcc-mcp:
    dcc: wwise
    layer: domain
    version: "0.1.0"
    tags: [wwise, waapi, audio, project]
    search-hint: "Wwise WAAPI project info query selected objects WAQL SoundBank generate validate save"
    tools: tools.yaml
---

# Wwise Project

Use these typed WAAPI tools to confirm the connected Wwise instance, inspect the
open project, query or inspect selected objects with bounded return fields,
generate SoundBanks, and save authoring changes. Use `wwise-audio` for imports,
Events, properties, and audible preview.
