from _validation import authoring_name, wav_file
from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_wwise.waapi import call_waapi


@skill_entry
def main(audio_file, name, folder="DCC MCP Showcase", import_operation="useExisting", **_kwargs):
    source = wav_file(audio_file)
    sound_name = authoring_name(name, "name")
    folder_name = authoring_name(folder, "folder")
    if import_operation not in {"createNew", "useExisting", "replaceExisting"}:
        raise ValueError("import_operation is not supported")

    result = call_waapi(
        "ak.wwise.core.audio.import",
        {
            "importOperation": import_operation,
            "default": {
                "importLocation": "\\Actor-Mixer Hierarchy\\Default Work Unit",
                "originalsSubFolder": "DccMcpShowcase",
            },
            "imports": [
                {
                    "audioFile": source,
                    "objectPath": f"<Folder>{folder_name}\\<Sound SFX>{sound_name}",
                }
            ],
        },
        options={"return": ["id", "name", "type", "path", "originalFilePath", "duration"]},
    )
    objects = result.get("objects", []) if isinstance(result, dict) else []
    if not objects:
        objects = result.get("return", []) if isinstance(result, dict) else []
    return skill_success(
        f"Imported Sound SFX '{sound_name}'.",
        name=sound_name,
        object_path=f"\\Actor-Mixer Hierarchy\\Default Work Unit\\{folder_name}\\{sound_name}",
        objects=objects,
    )


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
