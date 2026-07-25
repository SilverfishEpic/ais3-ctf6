"""Optional Claude SDK compatibility for offline Agent Bridge tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

try:  # pragma: no cover - exercised only when the real SDK is installed.
    from claude_agent_sdk import HookMatcher as HookMatcher
    from claude_agent_sdk import create_sdk_mcp_server as create_sdk_mcp_server
    from claude_agent_sdk import tool as tool
except ModuleNotFoundError:  # pragma: no cover - simple fallback for offline tests.

    @dataclass
    class HookMatcher:  # type: ignore[no-redef]
        """Minimal shape used by fake/offline tests when Claude SDK is unavailable."""

        hooks: list[Callable[..., Any]]

    def tool(name: str, description: str, schema: dict[str, Any]) -> Callable[[F], F]:
        def decorate(func: F) -> F:
            return func

        return decorate

    def create_sdk_mcp_server(
        *,
        name: str,
        version: str,
        tools: list[Callable[..., Any]],
    ) -> dict[str, Any]:
        return {"name": name, "version": version, "tools": tools}
