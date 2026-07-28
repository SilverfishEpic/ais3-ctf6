from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from agent_bridge.adapters.claude import ClaudeAdapter
from agent_bridge.adapters.fake import FakeAgentAdapter
from agent_bridge.errors import BridgeError, MemoryCoreError
from agent_bridge.loop import AgentLoop, LoopLimits, LoopOutcome, TurnRecord
from agent_bridge.memory.http import HttpMemoryClient
from agent_bridge.policy import BY_NAME, FULL, MemoryPolicy, SessionMode
from agent_bridge.protocols import AgentAdapter
from agent_bridge.runtime import (
    AgentStatus,
    make_stop_signal,
    process_is_running,
    read_status,
    request_stop,
    runtime_dir,
    stop_file,
    utc_now,
    write_status,
)
from agent_bridge.sandbox import ToolPolicy
from agent_bridge.skill import TOOL_NAMES, build_memory_server

from .config import (
    ClientConfig,
    config_path_from_args,
    load_saved_client_config,
    merge_saved_client_config,
    require_challenge,
    save_client_config,
)
from .errors import CONNECTION_ERROR, OK, SERVER_ERROR, USAGE_ERROR
from .render import emit_json, render_diff, render_error

AGENT_ERROR = 8
POLICY_ALIASES = {
    "full": "full",
    "facts": "fact_only",
    "fact_only": "fact_only",
    "facts-assumptions": "fact_and_assumption",
    "fact_and_assumption": "fact_and_assumption",
    "none": "no_memory",
    "no_memory": "no_memory",
}


def add_agent_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    agent = subparsers.add_parser("agent")
    agent_sub = agent.add_subparsers(dest="agent_command", required=True)

    run = agent_sub.add_parser("run")
    run.add_argument("prompt")
    run.add_argument("--mode", choices=["normal", "yolo"], default="normal")
    run.add_argument("--session", choices=["same", "new"], default="same")
    run.add_argument("--workspace", default=".")
    run.add_argument("--model")
    run.add_argument("--max-turns", type=int, default=10)
    run.add_argument("--max-cost-usd", type=float, default=5.0)
    run.add_argument("--idle-turns", type=int, default=3)
    run.add_argument(
        "--memory-policy",
        choices=sorted(POLICY_ALIASES),
        default="full",
        help=(
            "Memory exposure policy for the agent. Preferred values are "
            "full, facts, facts-assumptions, none; legacy internal names are "
            "also accepted."
        ),
    )
    run.add_argument("--guess-seed", type=int)
    run.add_argument("--fake-agent", metavar="SCENARIO")
    run.add_argument("--runtime-dir", default=None)
    run.add_argument("--no-memory-skill", action="store_true")
    run.set_defaults(handler=cmd_agent_run)

    init = agent_sub.add_parser(
        "init", help="create and save the explicitly approved agent workspace"
    )
    init.add_argument("path")
    init.set_defaults(handler=cmd_agent_init)

    stop = agent_sub.add_parser("stop")
    stop.add_argument("--runtime-dir", default=None)
    stop.set_defaults(handler=cmd_agent_stop)

    status = agent_sub.add_parser("status")
    status.add_argument("--runtime-dir", default=None)
    status.set_defaults(handler=cmd_agent_status)


def cmd_agent_init(args: argparse.Namespace, _config: ClientConfig, _client: object) -> int:
    """Create a named workspace and save it as a non-secret project default.

    There is deliberately no implicit ``./runs`` fallback here.  The directory can contain
    agent work products, so making the operator name it is also the approval that enables
    the web agent panel.
    """

    workspace = Path(args.path).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    path = config_path_from_args(args)
    save_client_config(
        path,
        merge_saved_client_config(
            load_saved_client_config(path), agent_workspace=str(workspace)
        ),
    )
    if getattr(args, "json", False):
        emit_json({"ok": True, "agent_workspace": str(workspace), "config": str(path)})
    else:
        print(f"ctfmem: agent workspace initialized at {workspace}")
        print("ctfmem: use `ctfmem /agent run <prompt>` or start ctfmem-web without a flag.")
    return OK


def cmd_agent_run(args: argparse.Namespace, config: ClientConfig, _client: object) -> int:
    try:
        return asyncio.run(_agent_run_async(args, config))
    except ValueError as exc:
        _emit_error({"type": "usage", "message": str(exc)}, config)
        return USAGE_ERROR
    except MemoryCoreError as exc:
        _emit_error(
            {
                "type": exc.code or "core_error",
                "status": exc.status,
                "message": str(exc),
                "details": exc.details,
            },
            config,
        )
        return CONNECTION_ERROR if exc.status is None else SERVER_ERROR
    except (BridgeError, OSError) as exc:
        _emit_error({"type": "agent_error", "message": str(exc)}, config)
        return AGENT_ERROR


async def _agent_run_async(args: argparse.Namespace, config: ClientConfig) -> int:
    challenge = require_challenge(config)
    policy = _memory_policy(args)
    session = SessionMode(args.session)
    stop = make_stop_signal(challenge, args.runtime_dir)
    stop.clear()

    started = utc_now()
    status = AgentStatus(
        challenge=challenge,
        mode=args.mode,
        session=args.session,
        status="running",
        started_at=started,
        updated_at=started,
        pid=os.getpid(),
    )
    write_status(status, args.runtime_dir)

    memory = HttpMemoryClient(config.api_url, timeout=config.timeout)
    adapter = None
    try:
        adapter = _build_adapter(args, memory=memory, challenge=challenge, policy=policy)
        loop = AgentLoop(
            adapter=adapter,
            memory=memory,
            challenge_id=challenge,
            policy=policy,
            session=session,
            limits=LoopLimits(
                max_turns=args.max_turns,
                max_cost_usd=args.max_cost_usd,
                idle_turns_before_stop=args.idle_turns,
            ),
            stop=stop,
        )
        if args.mode == "normal":
            record = await loop.run_once(args.prompt)
            status.status = "completed"
            status.turns = 1
            status.productive_turns = 1 if record.changed_board else 0
            status.last_revision = _revision_after(record.ingest)
            status.total_cost_usd = record.cost_usd
            status.stopped_because = "completed"
            status.updated_at = utc_now()
            write_status(status, args.runtime_dir)
            _emit_turn(record, config)
        else:
            outcome = await loop.run_yolo(args.prompt)
            status.status = "completed"
            status.turns = len(outcome.turns)
            status.productive_turns = outcome.productive_turns
            status.last_revision = _last_revision(outcome)
            status.total_cost_usd = outcome.total_cost_usd
            status.stopped_because = outcome.stopped_because
            status.updated_at = utc_now()
            write_status(status, args.runtime_dir)
            _emit_outcome(outcome, config)
        return OK
    finally:
        close = getattr(adapter, "close", None)
        if close is not None:
            await close()
        await memory.close()


def cmd_agent_stop(args: argparse.Namespace, config: ClientConfig, _client: object) -> int:
    try:
        challenge = require_challenge(config)
        path = request_stop(challenge, args.runtime_dir)
    except ValueError as exc:
        _emit_error({"type": "usage", "message": str(exc)}, config)
        return USAGE_ERROR
    if config.json_output:
        emit_json({"ok": True, "challenge": challenge, "stop_file": str(path)})
    else:
        print(f"Stop requested for {challenge}: {path}")
    return OK


def cmd_agent_status(args: argparse.Namespace, config: ClientConfig, _client: object) -> int:
    try:
        challenge = require_challenge(config)
    except ValueError as exc:
        _emit_error({"type": "usage", "message": str(exc)}, config)
        return USAGE_ERROR
    payload = read_status(challenge, args.runtime_dir)
    if payload is None:
        payload = {
            "challenge": challenge,
            "status": "not_started",
        }
    else:
        payload["running"] = process_is_running(payload.get("pid"))
        if payload.get("status") == "running" and not payload["running"]:
            payload["status"] = "stale"
    payload.setdefault("runtime_dir", str(runtime_dir(challenge, args.runtime_dir)))
    payload.setdefault("stop_file", str(stop_file(challenge, args.runtime_dir)))

    if config.json_output:
        emit_json({"ok": True, **payload})
    else:
        print(f"Agent: {payload.get('status')}")
        print(f"Challenge: {payload.get('challenge')}")
        if "mode" in payload:
            print(f"Mode: {payload.get('mode')}")
            print(f"Session: {payload.get('session')}")
            print(f"Turns: {payload.get('turns', 0)}")
            print(f"Last memory revision: {payload.get('last_revision')}")
            print(f"Reason: {payload.get('stopped_because')}")
        print(f"Stop file: {stop_file(challenge, args.runtime_dir)}")
    return OK


def _build_adapter(
    args: argparse.Namespace,
    *,
    memory: HttpMemoryClient,
    challenge: str,
    policy: MemoryPolicy,
) -> AgentAdapter:
    if args.fake_agent:
        return FakeAgentAdapter(args.fake_agent)

    mcp_servers: dict[str, Any] = {}
    extra_allowed_tools: list[str] = []
    if policy.skill_enabled and not args.no_memory_skill:
        mcp_servers["memory"] = build_memory_server(memory, challenge, policy)
        extra_allowed_tools.extend(TOOL_NAMES)

    return ClaudeAdapter(
        workspace=Path(args.workspace),
        tool_policy=ToolPolicy(),
        mcp_servers=mcp_servers,
        extra_allowed_tools=extra_allowed_tools,
        model=args.model,
        max_turns=args.max_turns,
        max_budget_usd=args.max_cost_usd,
        session_kind=args.session,
    )


def _memory_policy(args: argparse.Namespace) -> MemoryPolicy:
    policy_name = POLICY_ALIASES.get(args.memory_policy, "full")
    policy = BY_NAME.get(policy_name, FULL)
    if args.no_memory_skill:
        policy = policy.with_(skill_enabled=False)
    if args.guess_seed is not None:
        policy = policy.with_(guess_seed=args.guess_seed)
    return policy


def _emit_turn(record: TurnRecord, config: ClientConfig) -> None:
    if config.json_output:
        emit_json(
            {
                "ok": True,
                "mode": "normal",
                "turns": 1,
                "memory": record.ingest,
                "agent": {
                    "interrupted": record.result.interrupted,
                    "cost_usd": record.cost_usd,
                    "tool_calls": record.result.tool_calls,
                },
            }
        )
        return

    print("Agent run completed.")
    print("Mode: normal")
    print("Turns: 1")
    print(f"Cost: ${record.cost_usd:.4f}")
    print()
    render_diff(record.ingest)


def _emit_outcome(outcome: LoopOutcome, config: ClientConfig) -> None:
    if config.json_output:
        emit_json(
            {
                "ok": True,
                "mode": "yolo",
                "turns": len(outcome.turns),
                "productive_turns": outcome.productive_turns,
                "stopped_because": outcome.stopped_because,
                "total_cost_usd": outcome.total_cost_usd,
                "last_memory": outcome.turns[-1].ingest if outcome.turns else None,
            }
        )
        return

    print("Agent YOLO stopped.")
    print(f"Reason: {outcome.stopped_because}")
    print(f"Turns: {len(outcome.turns)}")
    print(f"Productive turns: {outcome.productive_turns}")
    print(f"Total cost: ${outcome.total_cost_usd:.4f}")
    if outcome.turns:
        print()
        render_diff(outcome.turns[-1].ingest)


def _revision_after(payload: dict[str, Any]) -> int | None:
    value = payload.get("revision_after")
    return value if isinstance(value, int) else None


def _last_revision(outcome: LoopOutcome) -> int | None:
    if not outcome.turns:
        return None
    return _revision_after(outcome.turns[-1].ingest)


def _emit_error(error: dict[str, Any], config: ClientConfig) -> None:
    if config.json_output:
        emit_json({"ok": False, "error": error})
    else:
        render_error(RuntimeError(str(error.get("message") or error.get("type"))), out=sys.stderr)
