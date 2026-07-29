import time

from dcc_mcp_core.skill import skill_entry, skill_success
from dcc_mcp_core.skills_helper import check_dcc_cancelled

from dcc_mcp_wwise.waapi import connect_waapi


def _preview_duration(client, target, requested):
    result = client.call(
        "ak.wwise.core.object.get",
        {"from": {"path": [target]}},
        options={"return": ["duration"]},
    )
    objects = result.get("return", []) if isinstance(result, dict) else []
    details = objects[0].get("duration") if objects else None
    if not isinstance(details, dict) or details.get("type") != "oneShot":
        return requested
    maximum = details.get("max")
    if not isinstance(maximum, (int, float)) or maximum <= 0:
        return requested
    margin = min(0.5, maximum * 0.25)
    return min(requested, max(0.01, maximum - margin))


@skill_entry
def main(object, duration_secs=3, **_kwargs):
    target = str(object).strip()
    duration = float(duration_secs)
    if not target or len(target) > 1024:
        raise ValueError("object must be a bounded Wwise object identifier or path")
    if not 0.25 <= duration <= 30:
        raise ValueError("duration_secs must be between 0.25 and 30")

    transport = None
    with connect_waapi() as client:
        preview_duration = _preview_duration(client, target, duration)
        created = client.call("ak.wwise.core.transport.create", {"object": target})
        transport = created.get("transport") if isinstance(created, dict) else None
        if transport is None:
            raise RuntimeError("Wwise did not create a preview transport")
        client.call(
            "ak.wwise.core.transport.executeAction",
            {"transport": transport, "action": "play"},
        )
        deadline = time.monotonic() + preview_duration
        while time.monotonic() < deadline:
            check_dcc_cancelled()
            time.sleep(min(0.1, deadline - time.monotonic()))

    return skill_success(
        f"Previewed the Wwise object for {preview_duration:g} seconds.",
        object=target,
        duration_secs=preview_duration,
        requested_duration_secs=duration,
        transport_released_on_disconnect=True,
    )


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
