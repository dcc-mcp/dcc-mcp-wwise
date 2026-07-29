from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_wwise.waapi import call_waapi


@skill_entry
def main(**_kwargs):
    info = call_waapi("ak.wwise.core.getInfo")
    return skill_success("Wwise WAAPI is ready.", info=info)


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
