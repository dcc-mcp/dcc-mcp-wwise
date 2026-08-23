import json
import os
import urllib.request

import pytest

from dcc_mcp_wwise import server, waapi


def test_resolve_host_pid_auto_selects_only_instance(monkeypatch):
    monkeypatch.delenv("DCC_MCP_WWISE_HOST_PID", raising=False)
    monkeypatch.setattr(server, "_detect_wwise_pids", lambda: [4321])
    assert server._resolve_host_pid(None) == 4321


def test_resolve_host_pid_rejects_ambiguous_instances(monkeypatch):
    monkeypatch.delenv("DCC_MCP_WWISE_HOST_PID", raising=False)
    monkeypatch.setattr(server, "_detect_wwise_pids", lambda: [1, 2])
    with pytest.raises(ValueError, match="Multiple Wwise"):
        server._resolve_host_pid(None)


def test_server_cli_leaves_waapi_url_unset_for_environment_resolution():
    args = server._parse_args([])

    assert args.waapi_url is None


def test_server_construction_is_host_bound(monkeypatch, tmp_path):
    monkeypatch.setenv("DCC_MCP_GATEWAY_PORT", "0")
    monkeypatch.setenv("DCC_MCP_REGISTRY_DIR", str(tmp_path / "registry"))
    monkeypatch.setenv("DCC_MCP_DISABLE_DEFAULT_SKILL_PATHS", "1")
    monkeypatch.setattr(
        waapi,
        "get_wwise_info",
        lambda _url=None: {"version": {"displayName": "2024.1.1.8691"}},
    )
    instance = server.WwiseMcpServer(host_pid=os.getpid())
    assert instance is not None


def test_direct_mcp_can_discover_and_load_project_skill(monkeypatch, tmp_path):
    monkeypatch.setenv("DCC_MCP_GATEWAY_PORT", "0")
    monkeypatch.setenv("DCC_MCP_REGISTRY_DIR", str(tmp_path / "registry"))
    monkeypatch.setenv("DCC_MCP_DISABLE_DEFAULT_SKILL_PATHS", "1")
    monkeypatch.setattr(waapi, "get_wwise_version", lambda _url=None: "v2024.1.1")
    monkeypatch.setattr(waapi, "is_connected", lambda _url=None: True)
    monkeypatch.setattr(server.WwiseMenu, "start", lambda _self: None)

    instance = server.WwiseMcpServer(port=0, host_pid=os.getpid())
    instance.register_builtin_actions()
    handle = instance.start()
    url = handle.mcp_url()
    session = None

    def post(payload):
        nonlocal session
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if session:
            headers["Mcp-Session-Id"] = session
        request = urllib.request.Request(url, json.dumps(payload).encode(), headers)
        with urllib.request.urlopen(request, timeout=5) as response:
            session = response.headers.get("Mcp-Session-Id") or session
            body = response.read().decode().strip()
        if body.startswith("event:") or "\ndata: " in body:
            body = next(line[6:] for line in body.splitlines() if line.startswith("data: "))
        return json.loads(body) if body else {}

    try:
        post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "wwise-smoke", "version": "0"},
                },
            }
        )
        post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        discovered = post(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "search_skills", "arguments": {"query": "Wwise project"}},
            }
        )
        assert "wwise-project" in json.dumps(discovered)
        loaded = post(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "load_skill",
                    "arguments": {"skill_name": "wwise-project"},
                },
            }
        )
        assert "wwise_project__ping" in json.dumps(loaded)
    finally:
        instance.stop()
