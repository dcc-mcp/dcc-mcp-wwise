"""Execution adapter for WAAPI-backed declarative skill scripts."""

from __future__ import annotations

from typing import Any, Callable


class WwiseWaapiDispatcher:
    """Run wrappers inline; Wwise owns authoring-thread dispatch behind WAAPI."""

    def dispatch_callable(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        for key in (
            "affinity",
            "context",
            "action_name",
            "skill_name",
            "execution",
            "timeout_hint_secs",
            "thread_affinity",
        ):
            kwargs.pop(key, None)
        return func(*args, **kwargs)
