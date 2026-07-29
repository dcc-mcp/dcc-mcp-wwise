import importlib.util
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


def _skill_script(name):
    script = (
        Path(__file__).parents[1]
        / "src"
        / "dcc_mcp_wwise"
        / "skills"
        / "wwise-project"
        / "scripts"
        / f"{name}.py"
    )
    spec = importlib.util.spec_from_file_location(f"wwise_{name}", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
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
