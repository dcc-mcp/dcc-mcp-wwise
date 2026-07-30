"""Session-scoped DCC-MCP menu for Wwise Authoring."""

from __future__ import annotations

import logging
import webbrowser
from typing import Any

from . import waapi

_LOGGER = logging.getLogger(__name__)
_COMMAND_URLS = {
    "dcc_mcp.wwise.repository": "https://github.com/dcc-mcp/dcc-mcp-wwise",
    "dcc_mcp.wwise.audio_showcase": "https://dcc-mcp.github.io/showcase/wwise",
}
_COMMANDS = [
    {
        "id": "dcc_mcp.wwise.repository",
        "displayName": "Project Repository",
        "mainMenu": {"basePath": "DCC-MCP"},
    },
    {
        "id": "dcc_mcp.wwise.audio_showcase",
        "displayName": "Audio Showcase",
        "mainMenu": {"basePath": "DCC-MCP"},
    },
]


class WwiseMenu:
    """Register menu commands while the adapter is connected to Wwise."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._client: Any = None
        self._subscription: Any = None

    def start(self) -> None:
        if self._client is not None:
            return
        client = None
        try:
            client = waapi._client_type()(self._url, allow_exception=True)
            subscription = client.subscribe("ak.wwise.ui.commands.executed", self._handle_command)
            command_ids = list(_COMMAND_URLS)
            client.call("ak.wwise.ui.commands.unregister", {"commands": command_ids}, options={})
            client.call("ak.wwise.ui.commands.register", {"commands": _COMMANDS}, options={})
        except Exception as exc:
            if client is not None:
                try:
                    client.disconnect()
                except Exception:
                    pass
            raise RuntimeError(f"Could not register the Wwise DCC-MCP menu: {exc}") from exc
        self._client = client
        self._subscription = subscription

    def stop(self) -> None:
        client, self._client = self._client, None
        subscription, self._subscription = self._subscription, None
        if client is None:
            return
        try:
            client.call(
                "ak.wwise.ui.commands.unregister",
                {"commands": list(_COMMAND_URLS)},
                options={},
            )
            if subscription is not None:
                client.unsubscribe(subscription)
        except Exception as exc:
            _LOGGER.warning("Could not remove the Wwise DCC-MCP menu: %s", exc)
        finally:
            try:
                client.disconnect()
            except Exception as exc:
                _LOGGER.warning("Could not disconnect the Wwise menu client: %s", exc)

    @staticmethod
    def _handle_command(*_args: Any, **payload: Any) -> None:
        url = _COMMAND_URLS.get(payload.get("command"))
        if url:
            webbrowser.open(url)
