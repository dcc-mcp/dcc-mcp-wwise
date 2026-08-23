import json
import sys

import pytest


class UnreachableWaapiClient:
    def __init__(self, _url, allow_exception):
        assert allow_exception is True
        raise RuntimeError("connection refused")


def test_root_help_discovers_doctor_verify_and_server_mode(capsys):
    from dcc_mcp_wwise import cli

    with pytest.raises(SystemExit) as raised:
        cli.main(["--help"])

    output = capsys.readouterr().out
    assert raised.value.code == 0
    assert "doctor" in output
    assert "verify" in output
    assert "--host-pid" in output


def test_public_doctor_reports_unreachable_waapi_as_structured_preflight(monkeypatch, capsys):
    from dcc_mcp_wwise import cli, waapi

    monkeypatch.setenv("DCC_MCP_WWISE_WAAPI_URL", "ws://127.0.0.1:8080/waapi")
    monkeypatch.delenv("DCC_MCP_WWISE_WAAPI_ALLOWED_HOSTS", raising=False)
    monkeypatch.setattr(waapi, "_client_type", lambda: UnreachableWaapiClient)

    code = cli.main(["doctor", "--json"])

    report = json.loads(capsys.readouterr().out)
    assert code == 10
    assert report["schema_version"] == 1
    assert report["status"] == "failed"
    assert report["dcc_type"] == "wwise"
    assert report["adapter_version"] == "0.1.2"
    assert report["core_version"]
    assert report["receipt_path"] is None
    assert report["checks"]["endpoint"] == {
        "success": True,
        "url": "ws://127.0.0.1:8080/waapi",
        "host": "127.0.0.1",
        "port": 8080,
        "allowed": True,
    }
    assert report["checks"]["runtime"]["success"] is False
    assert report["verify"]["directly_usable"] is False
    assert report["verify"]["failure_stage"] == "waapi_enablement"
    assert "connection refused" in report["verify"]["failure_reason"]
    assert len(report["next_steps"]) == 1
    assert report["next_steps"][0]["command"]


def test_public_doctor_uses_typed_get_info_to_prove_waapi_is_usable(monkeypatch, capsys):
    from dcc_mcp_wwise import cli, waapi

    class HealthyWaapiClient:
        calls = []

        def __init__(self, url, allow_exception):
            self.url = url
            assert allow_exception is True

        def call(self, uri, arguments, options):
            self.calls.append((self.url, uri, arguments, options))
            return {
                "displayName": "Wwise",
                "version": {"displayName": "2024.1.1.8691"},
            }

        def disconnect(self):
            return True

    HealthyWaapiClient.calls.clear()
    monkeypatch.setattr(waapi, "_client_type", lambda: HealthyWaapiClient)

    code = cli.main(["doctor", "--json"])

    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report["status"] == "ok"
    assert report["checks"]["core"] == {
        "success": True,
        "version": report["core_version"],
        "minimum": "0.19.86",
    }
    assert report["checks"]["runtime"] == {
        "success": True,
        "waapi_enabled": True,
        "client_allowed": True,
        "wwise_version": "2024.1.1.8691",
        "minimum_wwise_version": "2024.1",
    }
    assert report["verify"] == {
        "directly_usable": True,
        "failure_stage": None,
        "failure_reason": None,
    }
    assert report["next_steps"] == []
    assert HealthyWaapiClient.calls == [
        (
            "ws://127.0.0.1:8080/waapi",
            "ak.wwise.core.getInfo",
            {},
            {},
        )
    ]


def test_doctor_rejects_a_remote_endpoint_outside_the_operator_allowlist(monkeypatch, capsys):
    from dcc_mcp_wwise import cli, waapi

    class UnexpectedClient:
        def __init__(self, _url, _allow_exception):
            raise AssertionError("unapproved remote WAAPI must not be contacted")

    monkeypatch.delenv("DCC_MCP_WWISE_WAAPI_ALLOWED_HOSTS", raising=False)
    monkeypatch.setattr(waapi, "_client_type", lambda: UnexpectedClient)

    code = cli.main(["doctor", "--json", "--waapi-url", "ws://wwise.example.com:8080/waapi"])

    report = json.loads(capsys.readouterr().out)
    assert code == 10
    assert report["checks"]["endpoint"]["success"] is False
    assert report["checks"]["endpoint"]["allowed"] is False
    assert report["verify"]["failure_stage"] == "endpoint_allowlist"
    assert report["verify"]["directly_usable"] is False
    assert report["next_steps"][0]["command"] == [
        "dcc-mcp-wwise",
        "doctor",
        "--json",
        "--waapi-url",
        "ws://127.0.0.1:8080/waapi",
    ]


def test_verify_uses_the_same_typed_waapi_contract(monkeypatch, capsys):
    from dcc_mcp_wwise import cli, waapi

    class HealthyWaapiClient:
        def __init__(self, _url, allow_exception):
            assert allow_exception is True

        def call(self, uri, arguments, options):
            assert (uri, arguments, options) == ("ak.wwise.core.getInfo", {}, {})
            return {"version": {"displayName": "2024.1.1.8691"}}

        def disconnect(self):
            return True

    monkeypatch.setattr(waapi, "_client_type", lambda: HealthyWaapiClient)

    code = cli.main(["verify", "--json"])

    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report["verb"] == "verify"
    assert report["verify"]["directly_usable"] is True


def test_verify_failure_emits_a_verify_retry_command(monkeypatch, capsys):
    from dcc_mcp_wwise import cli, waapi

    monkeypatch.setattr(waapi, "_client_type", lambda: UnreachableWaapiClient)

    code = cli.main(["verify", "--json"])

    report = json.loads(capsys.readouterr().out)
    assert code == 10
    assert report["verb"] == "verify"
    assert report["next_steps"][0]["command"][1] == "verify"


def test_doctor_reports_an_invalid_endpoint_port_without_contacting_waapi(monkeypatch, capsys):
    from dcc_mcp_wwise import cli, waapi

    class UnexpectedClient:
        def __init__(self, _url, _allow_exception):
            raise AssertionError("invalid WAAPI configuration must fail before connection")

    monkeypatch.setattr(waapi, "_client_type", lambda: UnexpectedClient)

    code = cli.main(["doctor", "--json", "--waapi-url", "ws://127.0.0.1:not-a-port/waapi"])

    report = json.loads(capsys.readouterr().out)
    assert code == 10
    assert report["checks"]["endpoint"]["success"] is False
    assert report["verify"]["failure_stage"] == "configuration"
    assert report["next_steps"][0]["command"] == [
        "dcc-mcp-wwise",
        "doctor",
        "--json",
        "--waapi-url",
        "ws://127.0.0.1:8080/waapi",
    ]


def test_doctor_does_not_echo_rejected_url_credentials(monkeypatch, capsys):
    from dcc_mcp_wwise import cli, waapi

    class UnexpectedClient:
        def __init__(self, _url, _allow_exception):
            raise AssertionError("credential-bearing URL must not reach WAAPI")

    monkeypatch.setattr(waapi, "_client_type", lambda: UnexpectedClient)

    code = cli.main(
        [
            "doctor",
            "--json",
            "--waapi-url",
            "ws://operator:secret@127.0.0.1:8080/waapi",
        ]
    )

    output = capsys.readouterr().out
    report = json.loads(output)
    assert code == 10
    assert "operator" not in output
    assert "secret" not in output
    assert report["checks"]["endpoint"]["url"] is None


def test_doctor_uses_verify_exit_when_connected_waapi_rejects_the_typed_probe(monkeypatch, capsys):
    from dcc_mcp_wwise import cli, waapi

    class RejectingWaapiClient:
        def __init__(self, _url, allow_exception):
            assert allow_exception is True

        def call(self, _uri, _arguments, options):
            assert options == {}
            raise RuntimeError("getInfo is unavailable")

        def disconnect(self):
            return True

    monkeypatch.setattr(waapi, "_client_type", lambda: RejectingWaapiClient)

    code = cli.main(["doctor", "--json"])

    report = json.loads(capsys.readouterr().out)
    assert code == 40
    assert report["verify"]["failure_stage"] == "runtime"
    assert report["verify"]["directly_usable"] is False
    assert "getInfo is unavailable" in report["verify"]["failure_reason"]


def test_doctor_uses_verify_exit_for_a_malformed_runtime_version(monkeypatch, capsys):
    from dcc_mcp_wwise import cli, waapi

    class MalformedWaapiClient:
        def __init__(self, _url, allow_exception):
            assert allow_exception is True

        def call(self, _uri, _arguments, options):
            assert options == {}
            return {"version": {}}

        def disconnect(self):
            return True

    monkeypatch.setattr(waapi, "_client_type", lambda: MalformedWaapiClient)

    code = cli.main(["doctor", "--json"])

    report = json.loads(capsys.readouterr().out)
    assert code == 40
    assert report["verify"]["failure_stage"] == "runtime"
    assert report["checks"]["runtime"]["wwise_version"] == "unknown"


def test_doctor_rejects_wwise_below_the_supported_floor(monkeypatch, capsys):
    from dcc_mcp_wwise import cli, waapi

    class LegacyWaapiClient:
        def __init__(self, _url, allow_exception):
            assert allow_exception is True

        def call(self, _uri, _arguments, options):
            assert options == {}
            return {"version": {"displayName": "2023.1.5.8522"}}

        def disconnect(self):
            return True

    monkeypatch.setattr(waapi, "_client_type", lambda: LegacyWaapiClient)

    code = cli.main(["doctor", "--json"])

    report = json.loads(capsys.readouterr().out)
    assert code == 10
    assert report["verify"]["failure_stage"] == "wwise_version"
    assert report["checks"]["runtime"] == {
        "success": False,
        "waapi_enabled": True,
        "client_allowed": True,
        "wwise_version": "2023.1.5.8522",
        "minimum_wwise_version": "2024.1",
    }


def test_doctor_rejects_old_core_before_contacting_waapi(monkeypatch, capsys):
    from dcc_mcp_wwise import cli, install_contract, waapi

    class UnexpectedClient:
        def __init__(self, _url, _allow_exception):
            raise AssertionError("old Core must fail before WAAPI connection")

    monkeypatch.setattr(install_contract._core, "__version__", "0.19.85")
    monkeypatch.setattr(waapi, "_client_type", lambda: UnexpectedClient)

    code = cli.main(["doctor", "--json"])

    report = json.loads(capsys.readouterr().out)
    assert code == 10
    assert report["checks"]["core"] == {
        "success": False,
        "version": "0.19.85",
        "minimum": "0.19.86",
    }
    assert report["verify"]["failure_stage"] == "core"
    assert report["next_steps"][0]["command"][0] == sys.executable
