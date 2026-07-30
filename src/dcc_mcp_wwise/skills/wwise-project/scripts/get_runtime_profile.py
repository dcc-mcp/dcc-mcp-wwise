from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_wwise.waapi import call_waapi

_VOICE_FIELDS = [
    "pipelineID",
    "playingID",
    "gameObjectName",
    "objectGUID",
    "objectName",
    "baseVolume",
    "isStarted",
    "isVirtual",
]


@skill_entry
def main(cursor="capture", max_items=100, **_kwargs):
    if cursor not in {"capture", "user"}:
        raise ValueError("cursor must be capture or user")
    if isinstance(max_items, bool) or not isinstance(max_items, int) or not 1 <= max_items <= 500:
        raise ValueError("max_items must be an integer between 1 and 500")

    connection = call_waapi("ak.wwise.core.remote.getConnectionStatus")
    if not isinstance(connection, dict):
        raise RuntimeError("WAAPI returned an invalid runtime connection status")
    if not connection.get("isConnected"):
        return skill_success(
            "Wwise is not connected to a game Sound Engine.",
            connection=connection,
            voice_count=0,
            loaded_media_count=0,
            voices=[],
            loaded_media=[],
        )

    voices_result = call_waapi(
        "ak.wwise.core.profiler.getVoices",
        {"time": cursor},
        options={"return": _VOICE_FIELDS},
    )
    media_result = call_waapi("ak.wwise.core.profiler.getLoadedMedia", {"time": cursor})
    voices = voices_result.get("return", []) if isinstance(voices_result, dict) else []
    loaded_media = media_result.get("return", []) if isinstance(media_result, dict) else []
    return skill_success(
        "Retrieved the connected game Sound Engine profile.",
        connection=connection,
        cursor=cursor,
        voice_count=len(voices),
        loaded_media_count=len(loaded_media),
        voices=voices[:max_items],
        voices_truncated=len(voices) > max_items,
        loaded_media=loaded_media[:max_items],
        loaded_media_truncated=len(loaded_media) > max_items,
    )


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
