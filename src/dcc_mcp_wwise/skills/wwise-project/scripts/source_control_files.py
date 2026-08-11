from pathlib import Path

from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_wwise.waapi import call_waapi

_URIS = {
    "status": "ak.wwise.core.sourceControl.getStatus",
    "checkout": "ak.wwise.core.sourceControl.checkOut",
    "add": "ak.wwise.core.sourceControl.add",
    "revert": "ak.wwise.core.sourceControl.revert",
    "commit": "ak.wwise.core.sourceControl.commit",
}


@skill_entry
def main(action, files, message=None, **_kwargs):
    if action not in _URIS:
        raise ValueError("action is not supported")
    if not isinstance(files, list) or not 1 <= len(files) <= 256:
        raise ValueError("files must contain between 1 and 256 absolute paths")

    paths = []
    for value in files:
        path = Path(str(value)).expanduser()
        if not path.is_absolute():
            raise ValueError("source control file paths must be absolute")
        paths.append(str(path.resolve(strict=False)))
    if len(set(paths)) != len(paths):
        raise ValueError("source control file paths must be unique")

    arguments = {"files": paths}
    if action == "commit":
        description = str(message or "").strip()
        if not description or len(description) > 2048:
            raise ValueError("commit requires a message between 1 and 2048 characters")
        arguments["message"] = description
    elif message not in {None, ""}:
        raise ValueError("message is only accepted for commit")

    result = call_waapi(_URIS[action], arguments)
    return skill_success(
        f"Completed Wwise source control action '{action}'.",
        action=action,
        files=paths,
        result=result,
    )


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
