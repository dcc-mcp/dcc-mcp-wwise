import re

from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_wwise.waapi import call_waapi

_REFERENCE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")


@skill_entry
def main(object, reference, value, platform=None, **_kwargs):
    target = str(object).strip()
    reference_name = str(reference).strip()
    reference_value = str(value).strip()
    if not target or len(target) > 1024:
        raise ValueError("object must be a bounded Wwise object identifier or path")
    if not _REFERENCE.fullmatch(reference_name):
        raise ValueError("reference must be a valid Wwise reference name")
    if not reference_value or len(reference_value) > 1024:
        raise ValueError("value must be a bounded Wwise object identifier or path")

    arguments = {"object": target, "reference": reference_name, "value": reference_value}
    if platform:
        arguments["platform"] = str(platform).strip()
    call_waapi("ak.wwise.core.object.setReference", arguments)
    return skill_success(
        f"Set {reference_name} on the Wwise object.",
        object=target,
        reference=reference_name,
        value=reference_value,
        platform=arguments.get("platform"),
    )


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
