import re

from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_wwise.waapi import call_waapi

_NAME = re.compile(r'^[^\\/:*?"<>|.%]+$')
_GUID = re.compile(
    r"^\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}$"
)


def _strings(values, field, limit, length):
    items = [str(value).strip() for value in values or []]
    if len(items) > limit or any(not item or len(item) > length for item in items):
        raise ValueError(f"{field} must contain at most {limit} non-empty values")
    return items


@skill_entry
def main(
    soundbank,
    events=None,
    inclusions=None,
    platforms=None,
    languages=None,
    skip_languages=False,
    rebuild=False,
    write_to_disk=True,
    **_kwargs,
):
    name = str(soundbank).strip()
    if not name or len(name) > 128 or not _NAME.fullmatch(name):
        raise ValueError("soundbank contains characters Wwise does not allow")

    event_refs = _strings(events, "events", 256, 1024)
    if any(
        not (_GUID.fullmatch(value) or value.startswith("\\Events\\") or ":" in value)
        for value in event_refs
    ):
        raise ValueError("events must use Wwise GUIDs, qualified names, or Event project paths")
    inclusion_types = list(inclusions or ["event", "structure", "media"])
    if (
        not inclusion_types
        or len(set(inclusion_types)) != len(inclusion_types)
        or any(value not in {"event", "structure", "media"} for value in inclusion_types)
    ):
        raise ValueError("inclusions must contain unique event, structure, or media values")

    bank = {"name": name, "rebuild": bool(rebuild)}
    if event_refs:
        bank.update(events=event_refs, inclusions=inclusion_types)
    arguments = {
        "soundbanks": [bank],
        "skipLanguages": bool(skip_languages),
        "writeToDisk": bool(write_to_disk),
    }
    selected_platforms = _strings(platforms, "platforms", 16, 128)
    selected_languages = _strings(languages, "languages", 64, 128)
    if selected_platforms:
        arguments["platforms"] = selected_platforms
    if selected_languages:
        arguments["languages"] = selected_languages

    result = call_waapi("ak.wwise.core.soundbank.generate", arguments)
    logs = result.get("logs", []) if isinstance(result, dict) else []
    return skill_success(
        f"Generated SoundBank '{name}'.",
        soundbank=name,
        write_to_disk=bool(write_to_disk),
        log_count=len(logs),
        logs=logs[:100],
        logs_truncated=len(logs) > 100,
    )


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
