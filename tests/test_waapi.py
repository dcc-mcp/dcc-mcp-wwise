import importlib.util
from pathlib import Path

import pytest

from dcc_mcp_wwise import waapi


class FakeClient:
    calls = []

    def __init__(self, url, allow_exception):
        self.url = url
        self.allow_exception = allow_exception

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def disconnect(self):
        return True

    def call(self, uri, arguments, options):
        self.calls.append((self.url, uri, arguments, options))
        return {"version": {"displayName": "2024.1.1"}}


def test_call_waapi_uses_official_client_and_explicit_options(monkeypatch):
    FakeClient.calls.clear()
    monkeypatch.setattr(waapi, "_client_type", lambda: FakeClient)
    result = waapi.call_waapi(
        "ak.wwise.core.getInfo",
        {"value": 1},
        options={"return": ["name"]},
    )
    assert result["version"]["displayName"] == "2024.1.1"
    assert FakeClient.calls == [
        (
            "ws://127.0.0.1:8080/waapi",
            "ak.wwise.core.getInfo",
            {"value": 1},
            {"return": ["name"]},
        )
    ]


def test_call_error_is_not_misreported_as_connection_failure(monkeypatch):
    class FailingClient(FakeClient):
        def call(self, uri, arguments, options):
            raise ValueError("bad arguments")

    monkeypatch.setattr(waapi, "_client_type", lambda: FailingClient)
    with pytest.raises(RuntimeError, match=r"^WAAPI call failed"):
        waapi.call_waapi("ak.wwise.core.soundbank.generate")


def test_call_error_remains_primary_when_disconnect_also_fails(monkeypatch):
    class FailingClient(FakeClient):
        def call(self, uri, arguments, options):
            raise ValueError("primary call failure")

        def disconnect(self):
            raise ValueError("secondary disconnect failure")

    monkeypatch.setattr(waapi, "_client_type", lambda: FailingClient)

    with pytest.raises(waapi.WaapiCallError, match="primary call failure") as raised:
        waapi.call_waapi("ak.wwise.core.soundbank.generate")

    assert "secondary disconnect failure" not in str(raised.value)


@pytest.mark.parametrize(
    ("result", "primary_reason"),
    [
        (None, "returned no result"),
        ([], "returned a non-object result"),
    ],
)
def test_malformed_get_info_remains_primary_when_disconnect_also_fails(
    monkeypatch, result, primary_reason
):
    class MalformedClient(FakeClient):
        def call(self, uri, arguments, options):
            return result

        def disconnect(self):
            raise ValueError("secondary disconnect failure")

    monkeypatch.setattr(waapi, "_client_type", lambda: MalformedClient)

    with pytest.raises(waapi.WaapiCallError, match=primary_reason) as raised:
        waapi.get_wwise_info()

    assert "secondary disconnect failure" not in str(raised.value)


def _preview_module():
    script = (
        Path(__file__).parents[1]
        / "src"
        / "dcc_mcp_wwise"
        / "skills"
        / "wwise-audio"
        / "scripts"
        / "preview_object.py"
    )
    spec = importlib.util.spec_from_file_location("wwise_preview_object", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_preview_clamps_one_shot_before_its_natural_end():
    module = _preview_module()

    class Client:
        def call(self, uri, arguments, options):
            return {"return": [{"duration": {"type": "oneShot", "max": 1.8}}]}

    assert module._preview_duration(Client(), "\\Sound", 2) == pytest.approx(1.35)


@pytest.mark.parametrize("url", ["", "http://127.0.0.1:8080", "ws://missing-port/waapi"])
def test_resolve_waapi_url_rejects_invalid_urls(url, monkeypatch):
    monkeypatch.delenv("DCC_MCP_WWISE_WAAPI_URL", raising=False)
    if not url:
        assert waapi.resolve_waapi_url(url) == waapi.DEFAULT_WAAPI_URL
    else:
        with pytest.raises(ValueError):
            waapi.resolve_waapi_url(url)


def test_remote_waapi_requires_wss_and_an_explicit_operator_allowlist(monkeypatch):
    monkeypatch.setenv("DCC_MCP_WWISE_WAAPI_ALLOWED_HOSTS", "wwise.example.com")
    with pytest.raises(waapi.WaapiEndpointPolicyError, match="wss"):
        waapi.resolve_waapi_url("ws://wwise.example.com:8080/waapi")

    monkeypatch.delenv("DCC_MCP_WWISE_WAAPI_ALLOWED_HOSTS")
    with pytest.raises(waapi.WaapiEndpointPolicyError, match="allowlist"):
        waapi.resolve_waapi_url("wss://wwise.example.com:8080/waapi")

    monkeypatch.setenv("DCC_MCP_WWISE_WAAPI_ALLOWED_HOSTS", "wwise.example.com")
    assert (
        waapi.resolve_waapi_url("wss://wwise.example.com:8080/waapi")
        == "wss://wwise.example.com:8080/waapi"
    )


def test_waapi_endpoint_must_use_the_official_path(monkeypatch):
    monkeypatch.delenv("DCC_MCP_WWISE_WAAPI_URL", raising=False)

    with pytest.raises(ValueError, match="/waapi"):
        waapi.resolve_waapi_url("ws://127.0.0.1:8080/not-waapi")


def test_waapi_endpoint_rejects_url_credentials(monkeypatch):
    monkeypatch.delenv("DCC_MCP_WWISE_WAAPI_URL", raising=False)

    with pytest.raises(ValueError, match="credentials"):
        waapi.resolve_waapi_url("ws://user:secret@127.0.0.1:8080/waapi")
