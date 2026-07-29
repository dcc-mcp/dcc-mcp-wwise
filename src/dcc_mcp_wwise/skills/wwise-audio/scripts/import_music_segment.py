from _validation import authoring_name, wav_file
from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_wwise.waapi import call_waapi


@skill_entry
def main(audio_file, segment_name, on_name_conflict="merge", **_kwargs):
    source = wav_file(audio_file)
    name = authoring_name(segment_name, "segment_name")
    if on_name_conflict not in {"fail", "merge", "rename"}:
        raise ValueError("on_name_conflict is not supported")

    result = call_waapi(
        "ak.wwise.core.object.set",
        {
            "objects": [
                {
                    "object": "\\Interactive Music Hierarchy\\Default Work Unit",
                    "children": [
                        {
                            "type": "MusicSegment",
                            "name": name,
                            "import": {"files": [{"audioFile": source}]},
                        }
                    ],
                }
            ],
            "onNameConflict": on_name_conflict,
        },
        options={"return": ["id", "name", "type", "path"]},
    )
    return skill_success(
        f"Imported Music Segment '{name}'.",
        segment_name=name,
        object_path=f"\\Interactive Music Hierarchy\\Default Work Unit\\{name}",
        result=result,
    )


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
