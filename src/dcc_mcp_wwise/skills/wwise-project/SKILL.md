---
name: wwise-project
description: Inspect, query, validate, profile and capture a connected game runtime, reconcile generated SoundBanks, run bounded Work Unit source-control actions, and save Wwise projects through WAAPI.
license: MIT
compatibility: "Python 3.10+, Wwise 2024.1+"
metadata:
  dcc-mcp:
    dcc: wwise
    layer: domain
    version: "0.1.0"  # x-release-please-version
    tags: [wwise, waapi, audio, project]
    search-hint: "Wwise WAAPI project query runtime Sound Engine remote connect profiler capture voices SoundBank ProjectInfo reconcile Unity Unreal Work Unit source control checkout commit save"
    tools: tools.yaml
---

# Wwise Project

Use these typed WAAPI tools to confirm the connected Wwise instance, inspect the
open project, query or inspect selected objects with bounded return fields,
inspect a connected game Sound Engine profile, generate SoundBanks, and save
authoring changes. Use `wwise-audio` for imports, Game Sync authoring, RTPC
curves, Events, properties, and audible preview. Run source-control `status`
first and require explicit user approval before checkout, add, revert, or commit.
