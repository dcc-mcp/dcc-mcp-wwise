import importlib.util
import sys
from pathlib import Path

from dcc_mcp_wwise import menu


class FakeClient:
    instances = []

    def __init__(self, url, allow_exception):
        self.url = url
        self.allow_exception = allow_exception
        self.calls = []
        self.callback = None
        self.disconnected = False
        self.instances.append(self)

    def subscribe(self, uri, callback):
        self.callback = callback
        return "subscription"

    def call(self, uri, arguments, options):
        self.calls.append((uri, arguments, options))
        return {}

    def unsubscribe(self, subscription):
        assert subscription == "subscription"
        return True

    def disconnect(self):
        self.disconnected = True


def _skill_script(name, skill="wwise-project"):
    script = (
        Path(__file__).parents[1]
        / "src"
        / "dcc_mcp_wwise"
        / "skills"
        / skill
        / "scripts"
        / f"{name}.py"
    )
    spec = importlib.util.spec_from_file_location(f"wwise_{name}", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.path.insert(0, str(script.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def test_menu_registers_dispatches_and_cleans_up(monkeypatch):
    FakeClient.instances.clear()
    opened = []
    monkeypatch.setattr(menu.waapi, "_client_type", lambda: FakeClient)
    monkeypatch.setattr(menu.webbrowser, "open", opened.append)

    integration = menu.WwiseMenu("ws://127.0.0.1:8080/waapi")
    integration.start()
    client = FakeClient.instances[0]
    client.callback(command="dcc_mcp.wwise.audio_showcase")
    integration.stop()

    assert opened == ["https://dcc-mcp.github.io/showcase/wwise"]
    assert [call[0] for call in client.calls] == [
        "ak.wwise.ui.commands.unregister",
        "ak.wwise.ui.commands.register",
        "ak.wwise.ui.commands.unregister",
    ]
    assert client.disconnected


def test_generate_soundbank_sends_bounded_waapi_payload(monkeypatch):
    module = _skill_script("generate_soundbank")
    calls = []
    monkeypatch.setattr(
        module,
        "call_waapi",
        lambda uri, arguments: calls.append((uri, arguments)) or {"logs": []},
    )

    module.main(
        soundbank="Gameplay",
        events=["\\Events\\Default Work Unit\\Gameplay\\Play_UI_Confirm"],
        platforms=["Windows"],
        write_to_disk=False,
    )

    assert calls == [
        (
            "ak.wwise.core.soundbank.generate",
            {
                "soundbanks": [
                    {
                        "name": "Gameplay",
                        "rebuild": False,
                        "events": ["\\Events\\Default Work Unit\\Gameplay\\Play_UI_Confirm"],
                        "inclusions": ["event", "structure", "media"],
                    }
                ],
                "skipLanguages": False,
                "writeToDisk": False,
                "platforms": ["Windows"],
            },
        )
    ]


def test_import_variation_container_builds_one_object_set_payload(tmp_path, monkeypatch):
    first = tmp_path / "footstep-01.wav"
    second = tmp_path / "footstep-02.wav"
    first.touch()
    second.touch()
    module = _skill_script("import_variation_container", "wwise-audio")
    calls = []
    monkeypatch.setattr(
        module,
        "call_waapi",
        lambda uri, arguments, options=None: (
            calls.append((uri, arguments, options)) or {"objects": []}
        ),
    )

    module.main(
        container_name="Footsteps",
        audio_files=[
            {"audio_file": str(first), "name": "Footstep 01"},
            {"audio_file": str(second), "name": "Footstep 02"},
        ],
        folder="Gameplay",
        mode="random",
        play_mode="step",
    )

    assert calls == [
        (
            "ak.wwise.core.object.set",
            {
                "objects": [
                    {
                        "object": "\\Actor-Mixer Hierarchy\\Default Work Unit",
                        "children": [
                            {
                                "type": "Folder",
                                "name": "Gameplay",
                                "children": [
                                    {
                                        "type": "RandomSequenceContainer",
                                        "name": "Footsteps",
                                        "@RandomOrSequence": 1,
                                        "@PlayMechanismStepOrContinuous": 1,
                                        "children": [
                                            {
                                                "type": "Sound",
                                                "name": "Footstep 01",
                                                "import": {
                                                    "files": [{"audioFile": str(first.resolve())}]
                                                },
                                            },
                                            {
                                                "type": "Sound",
                                                "name": "Footstep 02",
                                                "import": {
                                                    "files": [{"audioFile": str(second.resolve())}]
                                                },
                                            },
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ],
                "onNameConflict": "merge",
                "listMode": "append",
            },
            {"return": ["id", "name", "type", "path"]},
        )
    ]


def test_set_object_reference_calls_official_waapi(monkeypatch):
    module = _skill_script("set_object_reference", "wwise-audio")
    calls = []
    monkeypatch.setattr(
        module,
        "call_waapi",
        lambda uri, arguments: calls.append((uri, arguments)) or {},
    )

    module.main(
        object="\\Actor-Mixer Hierarchy\\Default Work Unit\\Gameplay\\Footsteps",
        reference="OutputBus",
        value="\\Master-Mixer Hierarchy\\Default Work Unit\\Master Audio Bus\\SFX",
        platform="Windows",
    )

    assert calls == [
        (
            "ak.wwise.core.object.setReference",
            {
                "object": "\\Actor-Mixer Hierarchy\\Default Work Unit\\Gameplay\\Footsteps",
                "reference": "OutputBus",
                "value": "\\Master-Mixer Hierarchy\\Default Work Unit\\Master Audio Bus\\SFX",
                "platform": "Windows",
            },
        )
    ]


def test_runtime_profile_snapshot_is_bounded_to_connected_capture(monkeypatch):
    module = _skill_script("get_runtime_profile")
    calls = []

    def fake_call(uri, arguments=None, options=None):
        calls.append((uri, arguments, options))
        if uri == "ak.wwise.core.remote.getConnectionStatus":
            return {"isConnected": True, "status": "Connected", "console": {"name": "Game"}}
        if uri == "ak.wwise.core.profiler.getVoices":
            return {"return": [{"objectName": "Footsteps"}]}
        return {"return": [{"fileName": "123.wem", "size": 456}]}

    monkeypatch.setattr(module, "call_waapi", fake_call)
    result = module.main(cursor="capture", max_items=10)

    assert calls == [
        ("ak.wwise.core.remote.getConnectionStatus", None, None),
        (
            "ak.wwise.core.profiler.getVoices",
            {"time": "capture"},
            {
                "return": [
                    "pipelineID",
                    "playingID",
                    "gameObjectName",
                    "objectGUID",
                    "objectName",
                    "baseVolume",
                    "isStarted",
                    "isVirtual",
                ]
            },
        ),
        ("ak.wwise.core.profiler.getLoadedMedia", {"time": "capture"}, None),
    ]
    assert result["context"]["voice_count"] == 1
    assert result["context"]["loaded_media_count"] == 1


def test_author_switch_container_creates_music_states_and_assignments(tmp_path, monkeypatch):
    exploration = tmp_path / "exploration.wav"
    combat = tmp_path / "combat.wav"
    exploration.touch()
    combat.touch()
    module = _skill_script("author_switch_container", "wwise-audio")
    calls = []
    monkeypatch.setattr(
        module,
        "call_waapi",
        lambda uri, arguments, options=None: (
            calls.append((uri, arguments, options)) or {"objects": []}
        ),
    )

    result = module.main(
        container_name="RPG Music",
        group_name="Game Mode",
        variants=[
            {"value": "Exploration", "audio_file": str(exploration)},
            {"value": "Combat", "audio_file": str(combat)},
        ],
        game_sync_kind="state",
        content_kind="music",
        folder="RPG",
        default_value="Exploration",
    )

    object_set = calls[0][1]
    state_group = object_set["objects"][0]["children"][0]
    music_container = object_set["objects"][1]["children"][0]["children"][0]
    assert state_group == {
        "type": "StateGroup",
        "name": "Game_Mode",
        "children": [
            {"type": "State", "name": "Exploration"},
            {"type": "State", "name": "Combat"},
        ],
    }
    assert music_container["type"] == "MusicSwitchContainer"
    assert [child["type"] for child in music_container["children"]] == [
        "MusicSegment",
        "MusicSegment",
    ]
    assert music_container["@Arguments"] == ["\\States\\Default Work Unit\\Game_Mode"]
    assert music_container["@Entries"] == [
        {
            "type": "MultiSwitchEntry",
            "name": "",
            "@EntryPath": ["\\States\\Default Work Unit\\Game_Mode\\Exploration"],
            "@AudioNode": (
                "\\Interactive Music Hierarchy\\Default Work Unit\\RPG\\RPG Music\\Exploration"
            ),
            "children": [],
        },
        {
            "type": "MultiSwitchEntry",
            "name": "",
            "@EntryPath": ["\\States\\Default Work Unit\\Game_Mode\\Combat"],
            "@AudioNode": (
                "\\Interactive Music Hierarchy\\Default Work Unit\\RPG\\RPG Music\\Combat"
            ),
            "children": [],
        },
        {
            "type": "MultiSwitchEntry",
            "name": "",
            "@EntryPath": ["\\States\\Default Work Unit\\Game_Mode"],
            "@AudioNode": (
                "\\Interactive Music Hierarchy\\Default Work Unit\\RPG\\RPG Music\\Exploration"
            ),
            "children": [],
        },
    ]
    assert [call[0] for call in calls] == ["ak.wwise.core.object.set"]
    assert result["context"]["container_path"].endswith("\\RPG\\RPG Music")
    assert result["context"]["game_sync_path"].endswith("\\Game_Mode")


def test_author_switch_container_uses_switch_assignments_for_sfx(tmp_path, monkeypatch):
    stone = tmp_path / "stone.wav"
    grass = tmp_path / "grass.wav"
    stone.touch()
    grass.touch()
    module = _skill_script("author_switch_container", "wwise-audio")
    calls = []
    monkeypatch.setattr(
        module,
        "call_waapi",
        lambda uri, arguments, options=None: (
            calls.append((uri, arguments, options)) or {"objects": []}
        ),
    )

    module.main(
        container_name="RPG Footsteps",
        group_name="Surface Type",
        variants=[
            {"value": "Stone", "audio_file": str(stone)},
            {"value": "Grass", "audio_file": str(grass)},
        ],
    )

    assert [call[0] for call in calls] == [
        "ak.wwise.core.object.set",
        "ak.wwise.core.object.setReference",
        "ak.wwise.core.object.setReference",
        "ak.wwise.core.switchContainer.addAssignment",
        "ak.wwise.core.switchContainer.addAssignment",
    ]


def test_configure_rtpc_curve_creates_parameter_and_curve(monkeypatch):
    module = _skill_script("configure_rtpc_curve", "wwise-audio")
    calls = []
    monkeypatch.setattr(
        module,
        "call_waapi",
        lambda uri, arguments, options=None: (
            calls.append((uri, arguments, options)) or {"objects": []}
        ),
    )

    module.main(
        target="\\Interactive Music Hierarchy\\Default Work Unit\\RPG\\RPG Music",
        parameter_name="Combat Intensity",
        property="Volume",
        minimum=0,
        maximum=100,
        initial=0,
        points=[
            {"x": 0, "y": -12, "shape": "Linear"},
            {"x": 100, "y": 0, "shape": "SCurve"},
        ],
        parameter_folder="RPG",
    )

    parameter = calls[0][1]["objects"][0]["children"][0]["children"][0]
    rtpc = calls[1][1]["objects"][0]["@RTPC"][0]
    assert parameter == {
        "type": "GameParameter",
        "name": "Combat_Intensity",
        "@Min": 0.0,
        "@Max": 100.0,
        "@InitialValue": 0.0,
    }
    assert rtpc == {
        "type": "RTPC",
        "name": "",
        "@PropertyName": "Volume",
        "@ControlInput": "\\Game Parameters\\Default Work Unit\\RPG\\Combat_Intensity",
        "@Curve": {
            "type": "Curve",
            "points": [
                {"x": 0.0, "y": -12.0, "shape": "Linear"},
                {"x": 100.0, "y": 0.0, "shape": "SCurve"},
            ],
        },
    }
    assert [call[0] for call in calls] == [
        "ak.wwise.core.object.set",
        "ak.wwise.core.object.set",
    ]


def test_runtime_session_uses_official_remote_and_profiler_calls(monkeypatch):
    module = _skill_script("runtime_session")
    calls = []
    monkeypatch.setattr(
        module,
        "call_waapi",
        lambda uri, arguments=None: calls.append((uri, arguments)) or {"return": 42},
    )

    module.main(action="list")
    module.main(action="connect", host="127.0.0.1", app_name="RPG")
    module.main(action="start_capture")
    module.main(action="stop_capture")
    module.main(action="disconnect")

    assert calls == [
        ("ak.wwise.core.remote.getAvailableConsoles", None),
        (
            "ak.wwise.core.remote.connect",
            {"host": "127.0.0.1", "appName": "RPG"},
        ),
        ("ak.wwise.core.profiler.startCapture", None),
        ("ak.wwise.core.profiler.stopCapture", None),
        ("ak.wwise.core.remote.disconnect", None),
    ]


def test_source_control_files_routes_bounded_actions(monkeypatch, tmp_path):
    module = _skill_script("source_control_files")
    calls = []
    file = tmp_path / "RPG.wwu"
    file.touch()
    monkeypatch.setattr(
        module,
        "call_waapi",
        lambda uri, arguments: calls.append((uri, arguments)) or {"log": []},
    )

    module.main(action="status", files=[str(file)])
    module.main(action="commit", files=[str(file)], message="Update RPG audio")

    assert calls == [
        (
            "ak.wwise.core.sourceControl.getStatus",
            {"files": [str(file.resolve())]},
        ),
        (
            "ak.wwise.core.sourceControl.commit",
            {"files": [str(file.resolve())], "message": "Update RPG audio"},
        ),
    ]


def test_inspect_soundbank_delivery_requires_engine_handoff_metadata(tmp_path):
    module = _skill_script("inspect_soundbank_delivery")
    platform = tmp_path / "Windows"
    platform.mkdir()
    (tmp_path / "ProjectInfo.json").write_text(
        '{"ProjectInfo":{"Project":{"Name":"RPG"},"Platforms":[{"Name":"Windows",'
        '"Path":"Windows"}]}}',
        encoding="utf-8-sig",
    )
    (platform / "RPG.json").write_text(
        '{"SoundBanksInfo":{"Platform":"Windows","SoundBanks":[{"ShortName":"RPG",'
        '"Path":"RPG.bnk","Events":[{"Name":"Play_RPG"}],"Media":[{"Id":"1"}]}]}}',
        encoding="utf-8",
    )
    (platform / "RPG.bnk").write_bytes(b"bank")

    result = module.main(generated_soundbanks_dir=str(tmp_path))

    assert result["context"]["project"] == "RPG"
    assert result["context"]["platform_count"] == 1
    assert result["context"]["metadata_file_count"] == 1
    assert result["context"]["bank_count"] == 1
    assert result["context"]["event_count"] == 1
    assert result["context"]["media_count"] == 1
    assert result["context"]["missing_file_count"] == 0
    assert result["context"]["missing_files"] == []


def test_inspect_soundbank_delivery_rejects_platform_path_escape(tmp_path):
    module = _skill_script("inspect_soundbank_delivery")
    root = tmp_path / "GeneratedSoundBanks"
    root.mkdir()
    (root / "ProjectInfo.json").write_text(
        '{"ProjectInfo":{"Platforms":[{"Name":"Windows","Path":"../outside"}]}}',
        encoding="utf-8",
    )

    result = module.main(generated_soundbanks_dir=str(root))

    assert result["success"] is False
    assert "inside generated_soundbanks_dir" in result["message"]


def test_inspect_soundbank_delivery_rejects_bank_path_escape(tmp_path):
    module = _skill_script("inspect_soundbank_delivery")
    root = tmp_path / "GeneratedSoundBanks"
    platform = root / "Windows"
    platform.mkdir(parents=True)
    (root / "ProjectInfo.json").write_text(
        '{"ProjectInfo":{"Platforms":[{"Name":"Windows","Path":"Windows"}]}}',
        encoding="utf-8",
    )
    (platform / "RPG.json").write_text(
        '{"SoundBanksInfo":{"SoundBanks":[{"ShortName":"RPG","Path":"../../outside.bnk"}]}}',
        encoding="utf-8",
    )

    result = module.main(generated_soundbanks_dir=str(root))

    assert result["success"] is False
    assert "inside generated_soundbanks_dir" in result["message"]
