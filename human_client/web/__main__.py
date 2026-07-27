"""Launch the local web client.

``python -m human_client.web --templates ./templates``

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
from ..config import config_from_args
from .agent import AgentSettings
from .app import create_web_app
from .catalog import load_catalog

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
        "--templates",
        type=Path,
        required=True,
        help=(
            "Directory of template files, read once for technique titles and the template "
            "picker. Point it at the same directory Memory Core was started with."
        ),
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
    return parser


def _agent_settings(args: argparse.Namespace) -> AgentSettings | None:
    if args.agent_workspace is None:
        return None
    workspace = args.agent_workspace.resolve()
    if not workspace.is_dir():
        raise ValueError(f"agent workspace {workspace} does not exist")
    return AgentSettings(
        workspace=workspace,
        runtime_dir=args.agent_runtime_dir.resolve(),
        max_turns=args.agent_max_turns,
        max_cost_usd=args.agent_max_cost_usd,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = config_from_args(args)

    try:
        catalog = load_catalog(args.templates)
        agent = _agent_settings(args)
    except ValueError as exc:
        print(f"ctfmem-web: {exc}", file=sys.stderr)
        return 2

    app = create_web_app(
        client=MemoryCoreClient(config.api_url, timeout=config.timeout),
        summarizing_client=MemoryCoreClient(config.api_url, timeout=args.core_timeout),
        config=config,
        catalog=catalog,
        port=args.port,
        agent=agent,
    )

    print(f"ctfmem-web  http://{HOST}:{args.port}")
    print(f"  core      {config.api_url}")
    print(f"  actor     {config.actor}")
    print(f"  templates {catalog.directory} ({len(catalog.templates)} usable)")
    print(f"  timeouts  {config.timeout}s reads, {args.core_timeout}s summarizer routes")
    if agent is None:
        print("  agent     off (pass --agent-workspace to enable)")
    else:
        print(f"  agent     {agent.workspace}")
        print(
            f"            ceilings {agent.max_turns} turns, ${agent.max_cost_usd} "
            f"· runtime {agent.runtime_dir}"
        )
    for problem in catalog.problems:
        print(f"  warning   {problem}")

    uvicorn.run(app, host=HOST, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
