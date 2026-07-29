"""Launch the local web client.

``python -m human_client.web --api-url http://127.0.0.1:8777``

There is no ``--host`` flag. The server binds loopback by construction, because it is
unauthenticated and can write to the whiteboard; making the bind address configurable would
turn one careless flag into an open write API.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

from agent_bridge.runtime import DEFAULT_RUNTIME_DIR

from ..client import MemoryCoreClient
from ..config import config_from_args, config_path_from_args, load_saved_client_config
from .agent import ALL_POLICIES, FAKE_AGENTS, MODES, PREFERRED_POLICIES, SESSIONS, AgentSettings
from .app import create_web_app

HOST = "127.0.0.1"
DEFAULT_PORT = 8787
DEFAULT_CORE_TIMEOUT = 330.0

#: Ceilings for a web-started run. They match the CLI's own defaults, but they are this
#: server's policy rather than a mirror of it: the page may ask for less and never more.
DEFAULT_AGENT_MAX_TURNS = 10
DEFAULT_AGENT_MAX_COST_USD = 5.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m human_client.web",
        description="Serve the local human whiteboard UI against a running Memory Core.",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--api-url", default=None, help="Memory Core base URL.")
    parser.add_argument("--actor", default=None, help="Attributed to every write.")
    parser.add_argument(
        "--config",
        help="local non-secret defaults file (default: .ctfmem/client-config.json)",
    )
    parser.add_argument("--timeout", type=float, default=None, help="Read timeout, seconds.")
    parser.add_argument(
        "--core-timeout",
        type=float,
        default=DEFAULT_CORE_TIMEOUT,
        help=(
            "Read timeout for the routes that can reach the Summarizer: ingest, query, and "
            "creation with an initial input. It must be at least Memory Core's own "
            "summarizer read budget multiplied by the number of times an ingest may "
            "re-summarize after a concurrent commit."
        ),
    )
    parser.add_argument(
        "--agent-workspace",
        type=Path,
        default=None,
        help=(
            "Directory an agent run works in. Naming it is what enables agent runs at all; "
            "without it the routes refuse and the page hides the panel. The browser cannot "
            "override it."
        ),
    )
    parser.add_argument(
        "--agent-runtime-dir",
        type=Path,
        default=Path(DEFAULT_RUNTIME_DIR),
        help=(
            "Where run status and stop files live. Point it at the same place ctfmem uses "
            "so `ctfmem agent status` and `ctfmem agent stop` see runs started here."
        ),
    )
    parser.add_argument("--agent-max-turns", type=int, default=DEFAULT_AGENT_MAX_TURNS)
    parser.add_argument("--agent-max-cost-usd", type=float, default=DEFAULT_AGENT_MAX_COST_USD)
    parser.add_argument(
        "--agent-modes",
        default=",".join(MODES),
        help="Comma-separated ctfmem agent modes the page may offer.",
    )
    parser.add_argument(
        "--agent-sessions",
        default=",".join(SESSIONS),
        help="Comma-separated ctfmem agent session modes the page may offer.",
    )
    parser.add_argument(
        "--agent-memory-policies",
        default=",".join(PREFERRED_POLICIES),
        help=(
            "Comma-separated ctfmem agent memory policies the page may offer. "
            f"Allowed values: {', '.join(ALL_POLICIES)}."
        ),
    )
    parser.add_argument(
        "--agent-fake-agents",
        default=",".join(FAKE_AGENTS),
        help=(
            "Comma-separated recorded fake-agent scenarios the page may offer. "
            "Use an empty value to hide replay scenarios while leaving real Claude available."
        ),
    )
    return parser


def _agent_settings(args: argparse.Namespace) -> AgentSettings | None:
    """Resolve an explicit flag first, then the workspace approved by ``agent init``."""

    configured = load_saved_client_config(config_path_from_args(args)).agent_workspace
    raw_workspace = args.agent_workspace or configured
    if raw_workspace is None:
        return None
    workspace = Path(raw_workspace).expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError(f"agent workspace {workspace} does not exist")
    return AgentSettings(
        workspace=workspace,
        runtime_dir=args.agent_runtime_dir.resolve(),
        max_turns=args.agent_max_turns,
        max_cost_usd=args.agent_max_cost_usd,
        modes=_csv_choices(args.agent_modes),
        sessions=_csv_choices(args.agent_sessions),
        memory_policies=_csv_choices(args.agent_memory_policies),
        fake_agents=_csv_choices(args.agent_fake_agents, allow_empty=True),
    )


def _csv_choices(value: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    choices = tuple(part.strip() for part in value.split(",") if part.strip())
    if not choices and not allow_empty:
        raise ValueError("comma-separated option list must contain at least one value")
    return choices


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = config_from_args(args)

    try:
        agent = _agent_settings(args)
    except ValueError as exc:
        print(f"ctfmem-web: {exc}", file=sys.stderr)
        return 2

    app = create_web_app(
        client=MemoryCoreClient(config.api_url, timeout=config.timeout),
        summarizing_client=MemoryCoreClient(config.api_url, timeout=args.core_timeout),
        config=config,
        port=args.port,
        agent=agent,
    )

    print(f"ctfmem-web  http://{HOST}:{args.port}")
    print(f"  core      {config.api_url}")
    print(f"  actor     {config.actor}")
    print(f"  templates {config.api_url.rstrip('/')}/v1/templates")
    print(f"  timeouts  {config.timeout}s reads, {args.core_timeout}s summarizer routes")
    if agent is None:
        print(
            "  agent     off (run `ctfmem /agent init <path>` or pass --agent-workspace to enable)"
        )
    else:
        print(f"  agent     {agent.workspace}")
        print(
            f"            ceilings {agent.max_turns} turns, ${agent.max_cost_usd} "
            f"· runtime {agent.runtime_dir}"
        )
        print(f"            modes {','.join(agent.modes)} · sessions {','.join(agent.sessions)}")
        print(f"            memory {','.join(agent.memory_policies)}")
        if agent.fake_agents:
            print(f"            fake agents {','.join(agent.fake_agents)}")
    uvicorn.run(app, host=HOST, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
