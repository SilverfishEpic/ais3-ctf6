"""Optional Claude SDK compatibility for offline Agent Bridge tests.

This module deliberately exposes a small, project-owned surface instead of leaking the
Claude SDK's detailed generic types through the Agent Bridge.  The real SDK is used at
runtime when installed; offline tests get a minimal fallback with the same runtime shape.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar, cast

F = TypeVar("F", bound=Callable[..., Any])
HookCallback = Callable[[dict[str, Any], str | None, Any], Awaitable[dict[str, Any]]]


@dataclass
class HookMatcher:
    """Project-local hook matcher shape used by Agent Bridge code."""

    hooks: list[HookCallback]
    matcher: str | None = None
    timeout: float | None = None


_claude_sdk: Any | None

try:  # pragma: no cover - exercised only when the real SDK is installed.
    import claude_agent_sdk as _real_claude_sdk

    _claude_sdk = _real_claude_sdk
except ModuleNotFoundError:  # pragma: no cover - simple fallback for offline tests.
    _claude_sdk = None


def to_sdk_hook_matcher(matcher: HookMatcher) -> Any:
    """Convert the project-local hook matcher into the SDK object when available."""

    if _claude_sdk is None:
        return matcher
    return _claude_sdk.HookMatcher(
        matcher=matcher.matcher,
        hooks=cast(Any, matcher.hooks),
        timeout=matcher.timeout,
    )


def to_sdk_hooks(config: dict[str, list[HookMatcher]]) -> dict[str, Any]:
    return {
        event: [to_sdk_hook_matcher(matcher) for matcher in matchers]
        for event, matchers in config.items()
    }


def tool(name: str, description: str, schema: dict[str, Any]) -> Callable[[F], Any]:
    if _claude_sdk is not None:
        return cast(Callable[[F], Any], _claude_sdk.tool(name, description, schema))

    def decorate(func: F) -> F:
        return func

    return decorate


def create_sdk_mcp_server(
    *,
    name: str,
    version: str,
    tools: list[Callable[..., Any]],
) -> Any:
    if _claude_sdk is not None:
        return _claude_sdk.create_sdk_mcp_server(name, version=version, tools=cast(Any, tools))
    else:
        return {"name": name, "version": version, "tools": tools}
