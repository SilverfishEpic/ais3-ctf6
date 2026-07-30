from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from agent_bridge.adapters.registry import PROVIDERS, build_agent_adapter
from agent_bridge.completion import (
    Baseline,
    BaselineLimits,
    CompletionDetector,
    FlagStopConfig,
    build_flag_pattern,
    placeholder_reason,
    scan_roots,
    sha256_token,
)
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
#: How many rejected flag-shaped tokens the JSON output samples.
REJECTED_SAMPLE = 20
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
    run.add_argument(
        "--provider",
        choices=PROVIDERS,
        default=None,
        help=(
            "Agent provider. Defaults to CTFMEM_AGENT_PROVIDER or claude; "
            "--fake-agent selects fake when --provider is omitted."
        ),
    )
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
    _add_flagstop_arguments(run)
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


def _add_flagstop_arguments(run: argparse.ArgumentParser) -> None:
    """The "stop once a flag is obtained" brake.  Off unless asked for.

    Opt-in is implicit: naming a flag shape (``--flag-prefix`` / ``--flag-regex`` /
    ``--expect-flag-file``) arms the brake, ``--flagstop`` arms it with the generic shape,
    and ``--flagstop-off`` always wins.  A run that mentions none of them behaves exactly as
    it did before this brake existed, which is what the web UI and every existing script do.

    The inversion is deliberate.  The archived ablation data was produced by a wrapper whose
    argument list was ``--flag-prefix SEKAI ... --mode yolo``; keeping those exact spellings
    meaningful means an archived command reproduces the archived behaviour, and the two arms
    of the experiment differ by exactly one token (``--flagstop-off``).
    """

    group = run.add_argument_group(
        "flag stop",
        "Stop the YOLO loop once a tool prints a new, plausibly-shaped flag. Off by "
        "default; --mode yolo only. While armed the loop also stops after two consecutive "
        "provider failures (529 and friends), which produce no work and cost turns.",
    )
    group.add_argument(
        "--flagstop",
        action="store_true",
        help="arm the brake with the generic flag shape (prefer --flag-prefix)",
    )
    group.add_argument(
        "--flagstop-off",
        action="store_true",
        help="disarm the brake even if other flag options are present (ablation control arm)",
    )
    group.add_argument(
        "--flag-prefix",
        action="append",
        metavar="NAME",
        help="restrict the flag shape to this prefix, e.g. SEKAI (repeatable; recommended)",
    )
    group.add_argument(
        "--flag-regex",
        metavar="RE",
        help="override the flag pattern entirely (advanced; overrides --flag-prefix)",
    )
    group.add_argument(
        "--expect-flag-file",
        metavar="PATH",
        help=(
            "ground-truth flag file, read here and kept only as a sha256, never placed in "
            "argv. In this mode only a hash match stops the run, and a match that already "
            "existed in the workspace is reported as leaked, not solved."
        ),
    )
    group.add_argument(
        "--baseline-root",
        action="append",
        metavar="PATH",
        help=(
            "also scan this directory at t=0 (repeatable). The workspace is scanned by "
            "default, but the agent is not confined to it, so point this at anything else "
            "it can reach that already holds the answer - the challenge root above the "
            "workspace, or the Memory Core runs/ directory."
        ),
    )
    group.add_argument(
        "--no-baseline",
        action="store_true",
        help=(
            "skip the t=0 workspace scan. NOT recommended: 12 of the 20 challenges in "
            "test-challenge-set ship their real flag inside the challenge directory, and "
            "without the scan those score a false solve on turn 1."
        ),
    )
    group.add_argument("--baseline-max-file-bytes", type=int, default=4_000_000)
    group.add_argument("--baseline-max-total-bytes", type=int, default=400_000_000)
    group.add_argument(
        "--mask-flag",
        action="store_true",
        help="print a prefix plus a hash instead of the flag value",
    )


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
    # Validate every argument before anything is written: a broken --flag-regex or an empty
    # answer file is a usage error, and it must not land after agent-status.json already
    # says "running".  The t=0 scan stays below the status write - it reads whole trees and
    # can take a while, and the web UI polls that file to know the run started.
    flagstop = _flagstop_config(args)
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

    completion = _completion_detector(flagstop, args)

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
            completion=completion,
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
            status.stop_class = outcome.stop_class.value
            status.updated_at = utc_now()
            write_status(status, args.runtime_dir)
            _emit_outcome(outcome, config, completion)
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
            if payload.get("stop_class"):
                print(f"Stop class: {payload.get('stop_class')}")
        print(f"Stop file: {stop_file(challenge, args.runtime_dir)}")
    return OK


def _build_adapter(
    args: argparse.Namespace,
    *,
    memory: HttpMemoryClient,
    challenge: str,
    policy: MemoryPolicy,
) -> AgentAdapter:
    provider = _provider(args)
    return build_agent_adapter(
        provider=provider,
        fake_agent=args.fake_agent,
        workspace=args.workspace,
        model=args.model,
        max_turns=args.max_turns,
        max_cost_usd=args.max_cost_usd,
        session=args.session,
        memory=memory,
        challenge=challenge,
        policy=policy,
        no_memory_skill=args.no_memory_skill,
    )


def _provider(args: argparse.Namespace) -> str:
    if args.provider:
        return str(args.provider)
    if args.fake_agent:
        return "fake"
    return os.environ.get("CTFMEM_AGENT_PROVIDER", "claude")


def _flagstop_config(args: argparse.Namespace) -> FlagStopConfig | None:
    """Settle what the brake is looking for, or ``None`` when this run does not want one.

    ``None`` is the default and means the loop behaves exactly as it did before the brake
    existed.  ``--mode normal`` never gets one: it runs a single turn, so there is no second
    turn to save, and the t=0 scan would be paid for nothing.

    Everything here is cheap and everything here can fail on bad input, which is why it runs
    before any state is written.
    """

    if args.flagstop_off or args.mode != "yolo":
        return None
    armed = bool(args.flagstop or args.flag_prefix or args.flag_regex or args.expect_flag_file)
    if not armed:
        return None
    return FlagStopConfig(
        pattern=build_flag_pattern(args.flag_prefix, args.flag_regex),
        expect_sha256=_expected_flag_sha256(args.expect_flag_file),
        mask_flags=args.mask_flag,
    )


def _completion_detector(
    config: FlagStopConfig | None, args: argparse.Namespace
) -> CompletionDetector | None:
    """Pay for the t=0 scan and hand back the brake."""

    if config is None:
        return None
    baseline = Baseline()
    if not args.no_baseline:
        baseline = scan_roots(
            _baseline_roots(args),
            config.pattern,
            limits=BaselineLimits(
                max_file_bytes=args.baseline_max_file_bytes,
                max_total_bytes=args.baseline_max_total_bytes,
            ),
        )
    return CompletionDetector(config, baseline=baseline)


def _baseline_roots(args: argparse.Namespace) -> list[str]:
    """Where "was this already on disk before we started?" gets asked.

    The workspace is the default, but it is not the agent's reach: ``ToolPolicy`` applies no
    path restriction at all, and the layout the eval uses puts the answer in a *sibling* of
    the workspace (``.../challenge`` next to ``.../solution``).  Scanning only the workspace
    makes ``LEAKED`` unreachable for exactly the runs it exists to score - a turn-1
    ``cat ../solution/README.md`` would hash-match, be absent from the baseline, and report
    ``solved``.  So the ground-truth file's own directory is always scanned too, and
    ``--baseline-root`` covers whatever else is in reach.
    """

    roots = [args.workspace, *(args.baseline_root or [])]
    if args.expect_flag_file:
        roots.append(str(Path(args.expect_flag_file).parent))
    return roots


def _expected_flag_sha256(path: str | None) -> str | None:
    """Reduce a ground-truth flag file to a hash, here, in this process.

    There is deliberately no ``--expect-flag LITERAL``: ``cat`` is on the agent's allowlist
    and ``/proc/<ppid>/cmdline`` is world-readable, so an answer on the command line is an
    answer the agent can read.  The plaintext never leaves this function.
    """

    if not path:
        return None
    try:
        blob = Path(path).read_bytes()
    except OSError as exc:
        # A missing answer file is a mistake in the invocation, not an agent failure, so it
        # has to reach the caller as a usage error rather than as exit 8.
        raise ValueError(f"--expect-flag-file {path} cannot be read: {exc}") from exc
    try:
        # Strict, and BOM-aware.  ``errors="replace"`` silently hashes a *different* string
        # for a file with a BOM or a latin-1 byte, and the run then never stops on the true
        # flag and scores unsolved with no warning at all - the worst shape for an eval.
        text = blob.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"--expect-flag-file {path} is not valid UTF-8: {exc}") from exc
    for line in text.splitlines():
        expected = line.strip()
        if not expected:
            continue
        # Only the placeholder checks.  The rest of ``implausible`` is shape guessing, and
        # ground truth is the one input allowed to have an odd shape - the detector bypasses
        # that filter for it, so refusing the file here would contradict the bypass and
        # reject answers like ``SEKAI{hello world}`` that detection handles fine.
        why = placeholder_reason(expected)
        if why is not None:
            raise ValueError(
                f"--expect-flag-file {path} holds a placeholder ({why}); "
                "refusing to use it as ground truth"
            )
        return sha256_token(expected)
    raise ValueError(f"--expect-flag-file {path} is empty")


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


def _emit_outcome(
    outcome: LoopOutcome,
    config: ClientConfig,
    completion: CompletionDetector | None = None,
) -> None:
    if config.json_output:
        emit_json(
            {
                "ok": True,
                "mode": "yolo",
                "turns": len(outcome.turns),
                "productive_turns": outcome.productive_turns,
                "stopped_because": outcome.stopped_because,
                "stop_class": outcome.stop_class.value,
                "solved": outcome.solved,
                "total_cost_usd": outcome.total_cost_usd,
                "flagstop": _flagstop_payload(outcome, completion),
                "last_memory": outcome.turns[-1].ingest if outcome.turns else None,
            }
        )
        return

    print("Agent YOLO stopped.")
    print(f"Reason: {outcome.stopped_because}")
    print(f"Stop class: {outcome.stop_class.value}")
    verdict = outcome.verdict
    if verdict is not None and verdict.flag:
        print(f"Flag: {verdict.flag}")
        print(f"Evidence: {verdict.evidence}")
        print(
            "Note: a sighting proves a tool printed this, not that it is correct."
            if not verdict.verified
            else "Note: verified against --expect-flag-file (sha256)."
        )
    if outcome.completion_error:
        print(f"Warning: the flag-stop brake failed and was disabled: {outcome.completion_error}")
    print(f"Turns: {len(outcome.turns)}")
    print(f"Productive turns: {outcome.productive_turns}")
    print(f"Total cost: ${outcome.total_cost_usd:.4f}")
    if completion is not None:
        base = completion.baseline
        suffix = "" if base.complete else " (incomplete: scan hit a size limit)"
        print(
            f"Baseline: {len(base.tokens)} pre-existing flag-shaped token(s) "
            f"in {base.files} file(s){suffix}"
        )
    if outcome.turns:
        print()
        render_diff(outcome.turns[-1].ingest)


def _flagstop_payload(
    outcome: LoopOutcome, completion: CompletionDetector | None
) -> dict[str, Any]:
    """Everything an eval harness needs to score the run, on stdout rather than on disk."""

    if completion is None:
        return {"armed": False, "error": outcome.completion_error}
    verdict = outcome.verdict
    return {
        "armed": True,
        "ground_truth": completion.config.expect_sha256 is not None,
        "pattern": completion.config.pattern.pattern,
        "baseline_tokens": len(completion.baseline.tokens),
        "baseline_files": completion.baseline.files,
        "baseline_complete": completion.baseline.complete,
        "flag": verdict.flag if verdict else None,
        "verified": bool(verdict and verdict.verified),
        "evidence": verdict.evidence if verdict else None,
        "turn": verdict.turn if verdict else None,
        "rejected_count": len(completion.rejected),
        # Capped: one huge dump can hold hundreds of brace-shaped tokens, and this is a
        # sample for a human checking the brake did not misfire, not an inventory.
        "rejected": [
            {"token": completion.mask(item.token), "reason": item.reason}
            for item in completion.rejected[:REJECTED_SAMPLE]
        ],
        "error": outcome.completion_error,
    }


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
