"""Provider adapter construction for ``ctfmem agent run``."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from agent_bridge.adapters.claude import ClaudeAdapter
from agent_bridge.adapters.codex import CodexAdapter, CodexMemoryMCP
from agent_bridge.adapters.fake import FakeAgentAdapter
from agent_bridge.memory.http import HttpMemoryClient
from agent_bridge.policy import MemoryPolicy
from agent_bridge.protocols import AgentAdapter
from agent_bridge.sandbox import ToolPolicy
from agent_bridge.skill import TOOL_NAMES, build_memory_server

ProviderName = Literal["claude", "codex", "fake"]
PROVIDERS: tuple[ProviderName, ...] = ("claude", "codex", "fake")


def build_agent_adapter(
    *,
    provider: str,
    fake_agent: str | None,
    workspace: str | Path,
    model: str | None,
    max_turns: int,
    max_cost_usd: float | None,
    session: str,
    memory: HttpMemoryClient,
    challenge: str,
    policy: MemoryPolicy,
    no_memory_skill: bool,
) -> AgentAdapter:
    """Build the requested provider adapter.

    The registry is intentionally the only place that knows provider-specific construction
    details.  ``AgentLoop`` remains bound to the ``AgentAdapter`` protocol only.
    """

    if provider not in PROVIDERS:
        choices = ", ".join(PROVIDERS)
        raise ValueError(f"unknown agent provider {provider!r}; expected one of: {choices}")

    if provider == "fake":
        if not fake_agent:
            raise ValueError("--provider fake requires --fake-agent SCENARIO")
        return FakeAgentAdapter(fake_agent)

    if fake_agent:
        raise ValueError("--fake-agent can only be used with --provider fake")

    if provider == "codex":
        memory_mcp = None
        if policy.skill_enabled and not no_memory_skill:
            memory_mcp = CodexMemoryMCP(
                api_url=memory.base_url,
                challenge=challenge,
                policy=policy.name,
                timeout=30.0,
                guess_seed=policy.guess_seed,
            )
        return CodexAdapter(
            workspace=workspace,
            model=model,
            session_kind=session,  # type: ignore[arg-type]
            memory_mcp=memory_mcp,
        )

    mcp_servers: dict[str, Any] = {}
    extra_allowed_tools: list[str] = []
    if policy.skill_enabled and not no_memory_skill:
        mcp_servers["memory"] = build_memory_server(memory, challenge, policy)
        extra_allowed_tools.extend(TOOL_NAMES)

    return ClaudeAdapter(
        workspace=Path(workspace),
        tool_policy=ToolPolicy(),
        mcp_servers=mcp_servers,
        extra_allowed_tools=extra_allowed_tools,
        model=model,
        max_turns=max_turns,
        max_budget_usd=max_cost_usd,
        session_kind=session,  # type: ignore[arg-type]
    )
