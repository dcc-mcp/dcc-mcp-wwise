from _validation import authoring_name, target_path
from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_wwise.waapi import call_waapi


@skill_entry
def main(event_name, target, folder="DCC MCP Showcase", **_kwargs):
    name = authoring_name(event_name, "event_name")
    folder_name = authoring_name(folder, "folder")
    target_object = target_path(target)
    result = call_waapi(
        "ak.wwise.core.object.create",
        {
            "parent": "\\Events\\Default Work Unit",
            "type": "Folder",
            "name": folder_name,
            "onNameConflict": "merge",
            "children": [
                {
                    "type": "Event",
                    "name": name,
                    "children": [
                        {
                            "name": "",
                            "type": "Action",
                            "@ActionType": 1,
                            "@Target": target_object,
                        }
                    ],
                }
            ],
        },
    )
    return skill_success(
        f"Created Play Event '{name}'.",
        event_path=f"\\Events\\Default Work Unit\\{folder_name}\\{name}",
        target=target_object,
        result=result,
    )


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
