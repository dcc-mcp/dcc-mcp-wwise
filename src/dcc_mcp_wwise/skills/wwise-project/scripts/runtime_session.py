from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_wwise.waapi import call_waapi

_ACTIONS = {"list", "connect", "disconnect", "start_capture", "stop_capture"}


@skill_entry
def main(action, host=None, app_name=None, command_port=None, **_kwargs):
    if action not in _ACTIONS:
        raise ValueError("action is not supported")
    if action == "list":
        result = call_waapi("ak.wwise.core.remote.getAvailableConsoles")
        consoles = result.get("consoles", []) if isinstance(result, dict) else []
        return skill_success(
            f"Found {len(consoles)} available Sound Engine target(s).",
            action=action,
            consoles=consoles,
        )
    if action == "connect":
        target_host = str(host or "").strip()
        if not target_host or len(target_host) > 1024:
            raise ValueError("host must identify a bounded Sound Engine target")
        arguments = {"host": target_host}
        if app_name is not None:
            application = str(app_name).strip()
            if not application or len(application) > 256:
                raise ValueError("app_name must be a bounded application name")
            arguments["appName"] = application
        if command_port is not None:
            if (
                isinstance(command_port, bool)
                or not isinstance(command_port, int)
                or not 1 <= command_port <= 65535
                or "appName" not in arguments
            ):
                raise ValueError("command_port requires app_name and must be between 1 and 65535")
            arguments["commandPort"] = command_port
        call_waapi("ak.wwise.core.remote.connect", arguments)
        return skill_success(
            "Connected Wwise to the Sound Engine target.", action=action, **arguments
        )

    uri = {
        "disconnect": "ak.wwise.core.remote.disconnect",
        "start_capture": "ak.wwise.core.profiler.startCapture",
        "stop_capture": "ak.wwise.core.profiler.stopCapture",
    }[action]
    result = call_waapi(uri)
    return skill_success(
        f"Completed Wwise runtime action '{action}'.",
        action=action,
        capture_time=result.get("return") if isinstance(result, dict) else None,
    )


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
