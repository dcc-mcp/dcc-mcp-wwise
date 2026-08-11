# RPG Game Audio Workflow

Use `wwise-project` and `wwise-audio` together. Confirm every generated object
with `query_objects`; do not treat a successful call as audible proof.

## Authoring order

1. Load both Skills, then call `get_project_info`.
2. Build variation containers for repeated UI, impacts, ambience, footsteps,
   weapons, and creatures.
3. Build surface or weapon selection with `author_switch_container` using
   `game_sync_kind: switch` and `content_kind: sfx`.
4. Build exploration, combat, and boss music with `author_switch_container`
   using `game_sync_kind: state` and `content_kind: music`.
5. Add health, speed, tension, or combat-intensity curves with
   `configure_rtpc_curve`.
6. Create explicit Play Events, set Output Bus or Attenuation references, and
   preview the Events.
7. Save, generate platform SoundBanks, then call
   `inspect_soundbank_delivery` on `GeneratedSoundBanks`.
8. In an integrated game build, call `runtime_session` with `list`, connect to
   the selected target, start capture, inspect `get_runtime_profile`, then stop
   capture and disconnect.
9. Call `source_control_files` with `status`. Ask for explicit approval before
   checkout, add, revert, or commit.

## RPG acceptance checklist

- UI, footsteps, impacts, ambience, combat, creatures, and voice have named
  Events and routed output buses.
- Repeated SFX use variation; mutually exclusive gameplay values use Switches.
- Exploration, combat, and boss music use States with a defined default.
- At least one runtime Game Parameter drives a validated RTPC curve.
- The intended platform SoundBank contains the Events and has no missing bank
  files.
- A game Sound Engine capture proves active voices and loaded media.
- Work Unit status is known before team handoff.

Unity and Unreal consume the generated integration assets and metadata. These
Skills author and validate Wwise; they do not edit engine scenes, components,
Addressables, packaging settings, or source-control policy.

For Unity Addressables, the generated SoundBank root must be under `Assets`;
generate from Authoring or the Wwise Picker, then verify the imported Wwise
assets and platform groups in `AddressableAssetsData`. For Unreal, the
Generated Sound Banks setting must point to the root containing
`ProjectInfo.json`; use the Wwise Browser reconciliation columns to verify
GUID/ShortID-backed UAssets after `inspect_soundbank_delivery` passes.
