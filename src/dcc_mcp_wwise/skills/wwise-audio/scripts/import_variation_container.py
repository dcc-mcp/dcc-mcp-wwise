from collections.abc import Mapping

from _validation import authoring_name, wav_file
from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_wwise.waapi import call_waapi

_MODE = {"sequence": 0, "random": 1}
_PLAY_MODE = {"continuous": 0, "step": 1}


@skill_entry
def main(
    container_name,
    audio_files,
    folder="DCC MCP Showcase",
    mode="random",
    play_mode="step",
    **_kwargs,
):
    name = authoring_name(container_name, "container_name")
    folder_name = authoring_name(folder, "folder")
    if mode not in _MODE or play_mode not in _PLAY_MODE:
        raise ValueError("mode or play_mode is not supported")
    if not isinstance(audio_files, list) or not 2 <= len(audio_files) <= 32:
        raise ValueError("audio_files must contain between 2 and 32 WAV files")

    children = []
    for item in audio_files:
        if not isinstance(item, Mapping):
            raise ValueError("each audio_files item must be an object")
        children.append(
            {
                "type": "Sound",
                "name": authoring_name(item.get("name"), "audio_files.name"),
                "import": {"files": [{"audioFile": wav_file(item.get("audio_file"))}]},
            }
        )
    child_names = [child["name"] for child in children]
    if len(set(child_names)) != len(child_names):
        raise ValueError("audio_files names must be unique")

    result = call_waapi(
        "ak.wwise.core.object.set",
        {
            "objects": [
                {
                    "object": "\\Actor-Mixer Hierarchy\\Default Work Unit",
                    "children": [
                        {
                            "type": "Folder",
                            "name": folder_name,
                            "children": [
                                {
                                    "type": "RandomSequenceContainer",
                                    "name": name,
                                    "@RandomOrSequence": _MODE[mode],
                                    "@PlayMechanismStepOrContinuous": _PLAY_MODE[play_mode],
                                    "children": children,
                                }
                            ],
                        }
                    ],
                }
            ],
            "onNameConflict": "merge",
            "listMode": "append",
        },
        options={"return": ["id", "name", "type", "path"]},
    )
    return skill_success(
        f"Imported {len(children)} sounds into {mode} container '{name}'.",
        container_path=(f"\\Actor-Mixer Hierarchy\\Default Work Unit\\{folder_name}\\{name}"),
        mode=mode,
        play_mode=play_mode,
        sounds=child_names,
        result=result,
    )


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
