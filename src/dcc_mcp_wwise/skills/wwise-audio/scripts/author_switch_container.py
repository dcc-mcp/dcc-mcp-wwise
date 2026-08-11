from collections.abc import Mapping

from _validation import authoring_name, wav_file
from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_wwise.waapi import call_waapi

_GAME_SYNCS = {
    "switch": ("\\Switches\\Default Work Unit", "SwitchGroup", "Switch"),
    "state": ("\\States\\Default Work Unit", "StateGroup", "State"),
}


def _game_sync_name(value, field):
    return authoring_name(value, field).replace(" ", "_")


@skill_entry
def main(
    container_name,
    group_name,
    variants,
    game_sync_kind="switch",
    content_kind="sfx",
    folder="Gameplay",
    default_value=None,
    **_kwargs,
):
    container = authoring_name(container_name, "container_name")
    group = _game_sync_name(group_name, "group_name")
    folder_name = authoring_name(folder, "folder")
    if game_sync_kind not in _GAME_SYNCS or content_kind not in {"sfx", "music"}:
        raise ValueError("game_sync_kind or content_kind is not supported")
    if not isinstance(variants, list) or not 2 <= len(variants) <= 32:
        raise ValueError("variants must contain between 2 and 32 items")

    prepared = []
    for item in variants:
        if not isinstance(item, Mapping):
            raise ValueError("each variant must be an object")
        value = _game_sync_name(item.get("value"), "variants.value")
        object_name = authoring_name(item.get("name", value), "variants.name")
        prepared.append(
            {
                "value": value,
                "name": object_name,
                "audio_file": wav_file(item.get("audio_file")),
            }
        )
    values = [item["value"] for item in prepared]
    names = [item["name"] for item in prepared]
    if len(set(values)) != len(values) or len(set(names)) != len(names):
        raise ValueError("variant values and object names must be unique")

    default = _game_sync_name(default_value or values[0], "default_value")
    if default not in values:
        raise ValueError("default_value must match one variant value")
    default_child = next(item["name"] for item in prepared if item["value"] == default)

    sync_root, group_type, value_type = _GAME_SYNCS[game_sync_kind]
    content_root = (
        "\\Actor-Mixer Hierarchy\\Default Work Unit"
        if content_kind == "sfx"
        else "\\Interactive Music Hierarchy\\Default Work Unit"
    )
    group_path = f"{sync_root}\\{group}"
    container_path = f"{content_root}\\{folder_name}\\{container}"
    children = [
        {
            "type": "Sound" if content_kind == "sfx" else "MusicSegment",
            "name": item["name"],
            "import": {"files": [{"audioFile": item["audio_file"]}]},
        }
        for item in prepared
    ]
    authored_container = {
        "type": "SwitchContainer" if content_kind == "sfx" else "MusicSwitchContainer",
        "name": container,
        "children": children,
    }
    if content_kind == "music":
        authored_container.update(
            {
                "@Arguments": [group_path],
                "@Entries": [
                    {
                        "type": "MultiSwitchEntry",
                        "name": "",
                        "@EntryPath": [f"{group_path}\\{item['value']}"],
                        "@AudioNode": f"{container_path}\\{item['name']}",
                        "children": [],
                    }
                    for item in prepared
                ]
                + [
                    {
                        "type": "MultiSwitchEntry",
                        "name": "",
                        "@EntryPath": [group_path],
                        "@AudioNode": f"{container_path}\\{default_child}",
                        "children": [],
                    }
                ],
            }
        )
    result = call_waapi(
        "ak.wwise.core.object.set",
        {
            "objects": [
                {
                    "object": sync_root,
                    "children": [
                        {
                            "type": group_type,
                            "name": group,
                            "children": [
                                {"type": value_type, "name": item["value"]} for item in prepared
                            ],
                        }
                    ],
                },
                {
                    "object": content_root,
                    "children": [
                        {
                            "type": "Folder",
                            "name": folder_name,
                            "children": [authored_container],
                        }
                    ],
                },
            ],
            "onNameConflict": "merge",
            "listMode": "replaceAll" if content_kind == "music" else "append",
        },
        options={"return": ["id", "name", "type", "path"]},
    )
    if content_kind == "sfx":
        call_waapi(
            "ak.wwise.core.object.setReference",
            {
                "object": container_path,
                "reference": "SwitchGroupOrStateGroup",
                "value": group_path,
            },
        )
        call_waapi(
            "ak.wwise.core.object.setReference",
            {
                "object": container_path,
                "reference": "DefaultSwitchOrState",
                "value": f"{group_path}\\{default}",
            },
        )
        for item in prepared:
            call_waapi(
                "ak.wwise.core.switchContainer.addAssignment",
                {
                    "child": f"{container_path}\\{item['name']}",
                    "stateOrSwitch": f"{group_path}\\{item['value']}",
                },
            )
    return skill_success(
        f"Authored {content_kind} {game_sync_kind} container '{container}'.",
        container_path=container_path,
        game_sync_path=group_path,
        default_value=default,
        assignments=len(prepared),
        result=result,
    )


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
