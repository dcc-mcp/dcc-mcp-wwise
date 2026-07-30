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
