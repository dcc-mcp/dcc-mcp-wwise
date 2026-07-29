import re

from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_wwise.waapi import call_waapi

_PROPERTY = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")


@skill_entry
def main(object, property, value, platform=None, **_kwargs):
    target = str(object).strip()
    property_name = str(property).strip()
    if not target or len(target) > 1024:
        raise ValueError("object must be a bounded Wwise object identifier or path")
    if not _PROPERTY.fullmatch(property_name):
        raise ValueError("property must be a valid Wwise property name")
    if not isinstance(value, (bool, int, float, str)):
        raise ValueError("value must be a boolean, number, or string")

    arguments = {"object": target, "property": property_name, "value": value}
    if platform:
        arguments["platform"] = str(platform).strip()
    call_waapi("ak.wwise.core.object.setProperty", arguments)
    return skill_success(
        f"Set {property_name} on the Wwise object.",
        object=target,
        property=property_name,
        value=value,
        platform=arguments.get("platform"),
    )


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
