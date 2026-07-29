import re

from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_wwise.waapi import call_waapi

_RETURN_FIELD = re.compile(r"^[A-Za-z@][A-Za-z0-9_:@.]*$")


@skill_entry
def main(waql, return_fields=None, **_kwargs):
    query = str(waql).strip()
    if not query or len(query) > 4096:
        raise ValueError("waql must contain between 1 and 4096 characters")
    fields = list(return_fields or ["id", "name", "type", "path"])
    if not 1 <= len(fields) <= 32 or any(not _RETURN_FIELD.fullmatch(field) for field in fields):
        raise ValueError("return_fields must contain 1-32 valid Wwise return expressions")

    result = call_waapi(
        "ak.wwise.core.object.get",
        {"waql": query},
        options={"return": fields},
    )
    objects = result.get("return", []) if isinstance(result, dict) else []
    return skill_success(
        f"Retrieved {len(objects)} Wwise object(s).",
        count=len(objects),
        objects=objects,
    )


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
