---
name: wwise-audio
description: Author and preview typed Wwise audio objects through WAAPI, including Sound SFX imports, variation containers, Music Segments, play Events, properties, and object references. Use after wwise-project confirms the intended project.
license: MIT
compatibility: "Python 3.10+, Wwise 2024.1+"
metadata:
  dcc-mcp:
    dcc: wwise
    layer: domain
    version: "0.1.0"
    tags: [wwise, waapi, audio, sound-design, music]
    search-hint: "Wwise import sound effect SFX variation random sequence container music segment create play event set volume pitch output bus attenuation preview audio"
    tools: tools.yaml
---

# Wwise Audio

Use these typed tools for normal Wwise authoring. Confirm the active project
with `wwise-project` first. Imports accept existing local WAV files and create
named Wwise objects or variation containers under bounded default Work Unit
folders. Preview is bounded; its short-lived WAAPI connection owns and releases
the transport.
