import json
import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _fake_doctor_host_boundaries(monkeypatch):
    from dcc_mcp_wwise import doctor, process_identity, waapi

    identity = process_identity.WwiseProcessIdentity(4321, "Wwise.exe", "start-1")
    monkeypatch.setattr(process_identity, "observe_wwise_process", lambda _pid: identity)

    def in_process_get_info(url=None, *, timeout_secs=5.0):
        assert timeout_secs > 0

        def validate(result):
            doctor._typed_wwise_version(result)
            return result

        return waapi.call_waapi("ak.wwise.core.getInfo", url=url, result_validator=validate)

    monkeypatch.setattr(waapi, "get_wwise_info", in_process_get_info)


def test_install_contract_is_owned_by_formal_core_02014() -> None:
    from dcc_mcp_core.deployment import (
        INSTALL_EXIT_OK,
        INSTALL_EXIT_PREFLIGHT,
        INSTALL_EXIT_VERIFY,
        INSTALL_SOP_SCHEMA_VERSION,
        load_install_sop_schema,
    )

    from dcc_mcp_wwise import doctor

    assert doctor.MIN_CORE_VERSION == "0.20.14"
    assert doctor.SCHEMA_VERSION == INSTALL_SOP_SCHEMA_VERSION
    assert (doctor.EXIT_OK, doctor.EXIT_PREFLIGHT, doctor.EXIT_VERIFY) == (
        INSTALL_EXIT_OK,
        INSTALL_EXIT_PREFLIGHT,
        INSTALL_EXIT_VERIFY,
    )
    assert load_install_sop_schema()["properties"]["schema_version"]["const"] == 1
    assert not (
        Path(__file__).parents[1] / "src" / "dcc_mcp_wwise" / "install_contract.py"
    ).exists()


def test_public_report_validates_with_the_packaged_core_schema() -> None:
    from dcc_mcp_core.deployment import load_install_sop_schema
    from jsonschema import Draft202012Validator

    from dcc_mcp_wwise import doctor

    report = doctor.doctor_report(timeout_ms=0)
    assert report.pop("_exit_code") == doctor.EXIT_PREFLIGHT
    Draft202012Validator(load_install_sop_schema()).validate(report)


class UnreachableWaapiClient:
    def __init__(self, _url, allow_exception):
        assert allow_exception is True
        raise RuntimeError("connection refused token=secret C:/private/project.wproj")


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
    assert report["verify"]["failure_reason"] == ("The WAAPI connection could not be established")
    assert report["verify"]["failure_type"] == "connection_failed"
    assert "connection refused" not in json.dumps(report)
    assert "secret" not in json.dumps(report)
    assert "private" not in json.dumps(report)
    assert len(report["next_steps"]) == 1
    assert report["next_steps"][0]["command"]


def test_public_doctor_classifies_deadline_without_raw_details(monkeypatch, capsys):
    from dcc_mcp_wwise import cli, waapi

    monkeypatch.setattr(
        waapi,
        "get_wwise_info",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(waapi.WaapiTimeoutError()),
    )

    code = cli.main(["doctor", "--json", "--timeout-ms", "50"])

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert captured.err == ""
    assert code == 40
    assert report["verify"]["directly_usable"] is False
    assert report["verify"]["failure_stage"] == "deadline"
    assert report["verify"]["failure_type"] == "timeout"
    assert "token" not in captured.out
    assert "certificate" not in captured.out


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

    code = cli.main(["doctor", "--json", "--host-pid", "4321"])

    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report["status"] == "ok"
    assert report["checks"]["core"] == {
        "success": True,
        "version": report["core_version"],
        "minimum": "0.20.14",
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
        "failure_type": None,
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


def test_loopback_get_info_without_exact_host_identity_is_preflight_only(monkeypatch, capsys):
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

    code = cli.main(["doctor", "--json"])

    report = json.loads(capsys.readouterr().out)
    assert code == 10
    assert report["checks"]["runtime"]["success"] is True
    assert report["verify"]["directly_usable"] is False
    assert report["verify"]["failure_stage"] == "host_identity"
    assert report["verify"]["failure_type"] == "identity_unavailable"


def test_loopback_is_usable_only_after_same_exact_identity_is_recaptured(monkeypatch, capsys):
    from dcc_mcp_wwise import cli, process_identity, waapi

    class HealthyWaapiClient:
        def __init__(self, _url, allow_exception):
            assert allow_exception is True

        def call(self, uri, arguments, options):
            assert (uri, arguments, options) == ("ak.wwise.core.getInfo", {}, {})
            return {"version": {"displayName": "2024.1.1.8691"}}

        def disconnect(self):
            return True

    identity = process_identity.WwiseProcessIdentity(4321, "Wwise.exe", "start-1")
    observations = []

    def observe(pid):
        observations.append(pid)
        return identity

    monkeypatch.setattr(process_identity, "observe_wwise_process", observe)
    monkeypatch.setattr(waapi, "_client_type", lambda: HealthyWaapiClient)

    code = cli.main(["doctor", "--json", "--host-pid", "4321"])

    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert observations == [4321, 4321]
    assert report["checks"]["identity"] == {
        "success": True,
        "pid": 4321,
        "executable": "Wwise.exe",
        "started_at": "start-1",
    }
    assert report["verify"]["directly_usable"] is True


def test_explicit_wrong_executable_fails_before_waapi_io(monkeypatch, capsys):
    from dcc_mcp_wwise import cli, process_identity, waapi

    monkeypatch.setattr(
        process_identity,
        "observe_wwise_process",
        lambda _pid: (_ for _ in ()).throw(
            process_identity.ProcessIdentityError("identity_mismatch")
        ),
    )
    monkeypatch.setattr(
        waapi,
        "get_wwise_info",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("wrong executable must fail before WAAPI I/O")
        ),
    )

    code = cli.main(["doctor", "--json", "--host-pid", "4321"])

    report = json.loads(capsys.readouterr().out)
    assert code == 10
    assert report["verify"]["failure_stage"] == "host_identity"
    assert report["verify"]["failure_type"] == "identity_mismatch"


def test_pid_reuse_during_get_info_fails_closed(monkeypatch, capsys):
    from dcc_mcp_wwise import cli, process_identity, waapi

    before = process_identity.WwiseProcessIdentity(4321, "Wwise.exe", "start-1")
    reused = process_identity.WwiseProcessIdentity(4321, "Wwise.exe", "start-2")
    observations = iter((before, reused))
    monkeypatch.setattr(
        process_identity,
        "observe_wwise_process",
        lambda _pid: next(observations),
    )
    monkeypatch.setattr(
        waapi,
        "get_wwise_info",
        lambda *_args, **_kwargs: {"version": {"displayName": "2024.1.1.8691"}},
    )

    code = cli.main(["doctor", "--json", "--host-pid", "4321"])

    report = json.loads(capsys.readouterr().out)
    assert code == 10
    assert report["checks"]["identity"]["success"] is False
    assert report["verify"]["directly_usable"] is False
    assert report["verify"]["failure_stage"] == "host_identity"
    assert report["verify"]["failure_type"] == "identity_mismatch"


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


def test_remote_typed_probe_remains_preflight_until_adapter_is_bound_on_wwise_host(
    monkeypatch, capsys
):
    from dcc_mcp_wwise import cli, waapi

    class HealthyRemoteWaapiClient:
        def __init__(self, url, allow_exception):
            assert url == "wss://wwise.example.com:8080/waapi"
            assert allow_exception is True

        def call(self, uri, arguments, options):
            assert (uri, arguments, options) == ("ak.wwise.core.getInfo", {}, {})
            return {"version": {"displayName": "2024.1.1.8691"}}

        def disconnect(self):
            return True

    monkeypatch.setenv("DCC_MCP_WWISE_WAAPI_ALLOWED_HOSTS", "wwise.example.com")
    monkeypatch.setattr(waapi, "_client_type", lambda: HealthyRemoteWaapiClient)

    code = cli.main(
        [
            "doctor",
            "--json",
            "--waapi-url",
            "wss://wwise.example.com:8080/waapi",
        ]
    )

    report = json.loads(capsys.readouterr().out)
    assert code == 10
    assert report["status"] == "failed"
    assert report["checks"]["runtime"]["success"] is True
    assert report["verify"]["directly_usable"] is False
    assert report["verify"]["failure_stage"] == "host_binding"
    assert report["next_steps"] == [
        {
            "id": "start-adapter-on-wwise-host",
            "description": "Start the PID-bound adapter on the Wwise authoring host",
            "command": [
                "dcc-mcp-wwise",
                "--waapi-url",
                "ws://127.0.0.1:8080/waapi",
            ],
            "why": report["verify"]["failure_reason"],
            "execution_host": "wwise_host",
        }
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

    code = cli.main(["verify", "--json", "--host-pid", "4321"])

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


@pytest.mark.parametrize("waapi_url", ["", "   "])
def test_doctor_rejects_an_explicitly_empty_endpoint_without_contacting_waapi(
    monkeypatch, capsys, waapi_url
):
    from dcc_mcp_wwise import cli, waapi

    class UnexpectedClient:
        def __init__(self, _url, _allow_exception):
            raise AssertionError("empty endpoint configuration must fail before connection")

    monkeypatch.delenv("DCC_MCP_WWISE_WAAPI_URL", raising=False)
    monkeypatch.setattr(waapi, "_client_type", lambda: UnexpectedClient)

    code = cli.main(["doctor", "--json", "--waapi-url", waapi_url])

    report = json.loads(capsys.readouterr().out)
    assert code == 10
    assert report["checks"]["endpoint"]["success"] is False
    assert report["verify"]["failure_stage"] == "configuration"
    assert report["verify"]["directly_usable"] is False


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
            raise RuntimeError("getInfo is unavailable token=secret C:/private/project.wproj")

        def disconnect(self):
            return True

    monkeypatch.setattr(waapi, "_client_type", lambda: RejectingWaapiClient)

    code = cli.main(["doctor", "--json"])

    report = json.loads(capsys.readouterr().out)
    assert code == 40
    assert report["verify"]["failure_stage"] == "runtime"
    assert report["verify"]["directly_usable"] is False
    assert report["verify"]["failure_reason"] == "The WAAPI getInfo RPC failed"
    assert report["verify"]["failure_type"] == "rpc_failed"
    assert "getInfo is unavailable" not in json.dumps(report)
    assert "secret" not in json.dumps(report)
    assert "private" not in json.dumps(report)


def test_doctor_reports_disconnect_failure_as_one_structured_runtime_failure(monkeypatch, capsys):
    from dcc_mcp_wwise import cli, waapi

    class DisconnectingWaapiClient:
        def __init__(self, _url, allow_exception):
            assert allow_exception is True

        def call(self, _uri, _arguments, options):
            assert options == {}
            return {"version": {"displayName": "2024.1.1.8691"}}

        def disconnect(self):
            raise ValueError("disconnect failed certificate=C:/private/client.pem")

    monkeypatch.setattr(waapi, "_client_type", lambda: DisconnectingWaapiClient)

    code = cli.main(["doctor", "--json"])

    captured = capsys.readouterr()
    assert captured.err == ""
    assert len(captured.out.splitlines()) == 1
    report = json.loads(captured.out)
    assert code == 40
    assert report["status"] == "failed"
    assert report["verify"]["failure_stage"] == "runtime"
    assert report["verify"]["directly_usable"] is False
    assert report["verify"]["failure_reason"] == "The WAAPI probe cleanup failed"
    assert report["verify"]["failure_type"] == "cleanup_failed"
    assert "disconnect failed" not in json.dumps(report)
    assert "certificate" not in json.dumps(report)
    assert "private" not in json.dumps(report)


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


@pytest.mark.parametrize(
    ("result", "private_detail"),
    [
        (None, "returned no result"),
        ([], "returned a non-object result"),
        ({"version": {"displayName": "2024.1"}}, "valid Wwise version"),
    ],
)
def test_malformed_runtime_result_remains_primary_when_disconnect_also_fails(
    monkeypatch, capsys, result, private_detail
):
    from dcc_mcp_wwise import cli, waapi

    class MalformedWaapiClient:
        def __init__(self, _url, allow_exception):
            assert allow_exception is True

        def call(self, _uri, _arguments, options):
            assert options == {}
            return result

        def disconnect(self):
            raise ValueError("secondary disconnect failure")

    monkeypatch.setattr(waapi, "_client_type", lambda: MalformedWaapiClient)

    code = cli.main(["doctor", "--json"])

    report = json.loads(capsys.readouterr().out)
    assert code == 40
    assert report["verify"]["failure_stage"] == "runtime"
    assert report["verify"]["failure_type"] in {"rpc_failed", "invalid_result"}
    output = json.dumps(report)
    assert private_detail not in output
    assert "secondary disconnect failure" not in output


@pytest.mark.parametrize(
    "version",
    [
        ["2024.1.1.8691"],
        2024.1,
        {"displayName": {"value": "2024.1.1.8691"}},
        {"displayName": ""},
        {"displayName": "   "},
        {"displayName": "Wwise 2024.1.1.8691"},
        {"name": "2024.1.1.8691 beta"},
        {"displayName": "2024.1"},
        {"displayName": "2024.1.1"},
        {"displayName": "02024.1.1.8691"},
        {"displayName": "2024.01.1.8691"},
        {"displayName": "2024.1.01.8691"},
        {"displayName": "2024.1.1.08691"},
        {"displayName": " 2024.1.1.8691"},
        {"displayName": "2024.1.1.8691 "},
        {"displayName": "2\u06602\u0664.1.1.8\u0666\u0669\u0661"},
        {"displayName": "2024.1.1." + ("9" * 100)},
        "2024.1.1.8691",
    ],
)
def test_doctor_fails_closed_for_noncanonical_typed_runtime_versions(monkeypatch, capsys, version):
    from dcc_mcp_wwise import cli, waapi

    class MalformedWaapiClient:
        def __init__(self, _url, allow_exception):
            assert allow_exception is True

        def call(self, _uri, _arguments, options):
            assert options == {}
            return {"version": version}

        def disconnect(self):
            return True

    monkeypatch.setattr(waapi, "_client_type", lambda: MalformedWaapiClient)

    code = cli.main(["doctor", "--json"])

    report = json.loads(capsys.readouterr().out)
    assert code == 40
    assert report["status"] == "failed"
    assert report["verify"]["failure_stage"] == "runtime"
    assert report["verify"]["directly_usable"] is False


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
    from dcc_mcp_wwise import cli, doctor, waapi

    class UnexpectedClient:
        def __init__(self, _url, _allow_exception):
            raise AssertionError("old Core must fail before WAAPI connection")

    monkeypatch.setattr(doctor._core, "__version__", "0.20.13")
    monkeypatch.setattr(waapi, "_client_type", lambda: UnexpectedClient)

    code = cli.main(["doctor", "--json"])

    report = json.loads(capsys.readouterr().out)
    assert code == 10
    assert report["checks"]["core"] == {
        "success": False,
        "version": "0.20.13",
        "minimum": "0.20.14",
    }
    assert report["verify"]["failure_stage"] == "core"
    assert report["next_steps"][0]["command"][0] == sys.executable
