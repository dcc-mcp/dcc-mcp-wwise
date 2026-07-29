from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_wwise.waapi import call_waapi


@skill_entry
def main(**_kwargs):
    project = call_waapi("ak.wwise.core.getProjectInfo")
    return skill_success("Retrieved the active Wwise project.", project=project)


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
