"""Starting ``ctfmem agent run`` as a child process, and reading back what it did.

The web UI does not orchestrate an agent. It assembles an argument list, starts the CLI,
and then reads the same status and stop files the CLI itself uses, so a run started from a
browser is visible to ``ctfmem agent status`` and can be stopped by ``ctfmem agent stop``,
and a run started from a terminal is visible to the page. There is one implementation of an
agent run and this module executes it verbatim.

Why the loop must not run inside the server, and the guardrails the capability comes with,
are recorded in ``docs/adr/0002-web-agent-runs.md``.

This is the only module in the package that starts a process. It passes an argument list
directly to the operating system and never asks a command interpreter to parse a string.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_bridge.adapters.fake import FakeAgentAdapter
from agent_bridge.runtime import (
    process_is_running,
    read_status,
    request_stop,
    runtime_dir,
    stop_file,
)

from ..agent_cli import POLICY_ALIASES

PACKAGE_ROOT = Path(__file__).resolve().parents[2]

LOG_NAME = "agent-run.log"
LOG_TAIL_BYTES = 8000

#: Mirrors the choices on ``ctfmem agent run``. ``test_web_agent`` feeds every argument list
#: this module builds through the CLI's own parser, so a change there fails here.
MODES = ("normal", "yolo")
SESSIONS = ("same", "new")

#: The four documented policy names. The CLI also accepts legacy internal spellings; the
#: page has no reason to offer them, but anything the CLI accepts is passed through.
PREFERRED_POLICIES = ("full", "facts", "facts-assumptions", "none")

SpawnAgent = Callable[[list[str], Path], int]


@dataclass(frozen=True)
class AgentSettings:
    """Everything about a run the browser is not allowed to choose."""

    workspace: Path
    runtime_dir: Path
    max_turns: int
    max_cost_usd: float

    def to_json(self) -> dict[str, Any]:
        return {
            "workspace": str(self.workspace),
            "runtime_dir": str(self.runtime_dir),
            "max_turns": self.max_turns,
            "max_cost_usd": self.max_cost_usd,
        }


def options(settings: AgentSettings | None) -> dict[str, Any]:
    """What the page may offer. ``enabled`` is false unless a workspace was set at launch."""

    if settings is None:
        return {"enabled": False, "reason": "start ctfmem-web with --agent-workspace to enable"}
    return {
        "enabled": True,
        "modes": list(MODES),
        "sessions": list(SESSIONS),
        "policies": list(PREFERRED_POLICIES),
        "scenarios": FakeAgentAdapter.available(),
        **settings.to_json(),
    }


def log_path(settings: AgentSettings, challenge_id: str) -> Path:
    return runtime_dir(challenge_id, settings.runtime_dir) / LOG_NAME


def build_argv(
    settings: AgentSettings,
    body: dict[str, Any],
    *,
    api_url: str,
    actor: str,
    challenge_id: str,
) -> list[str]:
    """Assemble the child's arguments, clamping anything the browser may only lower.

    Raises ``ValueError`` for a choice the CLI would reject anyway. Letting those through
    would mean a child that exits with a usage error into a log file nobody is watching,
    which reads to the operator as nothing happening at all.
    """

    prompt = body.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("a prompt is required")

    mode = _one_of(body.get("mode", "normal"), MODES, "mode")
    session = _one_of(body.get("session", "same"), SESSIONS, "session")
    policy = _one_of(body.get("memory_policy", "full"), tuple(sorted(POLICY_ALIASES)), "policy")

    turns = _clamp_int(body.get("max_turns"), settings.max_turns)
    cost = _clamp_float(body.get("max_cost_usd"), settings.max_cost_usd)

    argv = [
        sys.executable,
        "-m",
        "human_client.cli",
        "--api-url",
        api_url,
        "--challenge",
        challenge_id,
        "--actor",
        actor,
        "--json",
        "agent",
        "run",
        prompt,
        "--mode",
        mode,
        "--session",
        session,
        "--workspace",
        str(settings.workspace),
        "--max-turns",
        str(turns),
        "--max-cost-usd",
        str(cost),
        "--memory-policy",
        policy,
        "--runtime-dir",
        str(settings.runtime_dir),
    ]

    scenario = body.get("fake_agent")
    if scenario:
        available = FakeAgentAdapter.available()
        argv += ["--fake-agent", _one_of(scenario, tuple(available), "fake_agent")]

    return argv


def start(
    settings: AgentSettings,
    body: dict[str, Any],
    *,
    api_url: str,
    actor: str,
    challenge_id: str,
    spawn: SpawnAgent,
) -> dict[str, Any]:
    """Launch one run. Refuses while a live one is recorded for this challenge."""

    argv = build_argv(settings, body, api_url=api_url, actor=actor, challenge_id=challenge_id)

    path = log_path(settings, challenge_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")

    pid = spawn(argv, path)
    return {"challenge": challenge_id, "pid": pid, "log": str(path), "argv": argv}


def is_running(settings: AgentSettings, challenge_id: str) -> bool:
    payload = read_status(challenge_id, settings.runtime_dir)
    if payload is None or payload.get("status") != "running":
        return False
    return process_is_running(payload.get("pid"))


def status(settings: AgentSettings, challenge_id: str) -> dict[str, Any]:
    """The same payload ``ctfmem agent status`` prints, plus the child's output so far.

    ``running`` requires the recorded status to still say so, not merely that the pid
    resolves. A child this server started is nobody's to wait on, so it lingers as a zombie
    after it exits, and a zombie answers ``os.kill(pid, 0)`` exactly like a live process.
    Asking the pid alone would report every finished run as still running for as long as the
    server is up.
    """

    reap()
    payload = read_status(challenge_id, settings.runtime_dir)
    if payload is None:
        payload = {"challenge": challenge_id, "status": "not_started"}
    else:
        claims_running = payload.get("status") == "running"
        payload["running"] = claims_running and process_is_running(payload.get("pid"))
        if claims_running and not payload["running"]:
            payload["status"] = "stale"

    payload.setdefault("running", False)
    payload["runtime_dir"] = str(runtime_dir(challenge_id, settings.runtime_dir))
    payload["stop_file"] = str(stop_file(challenge_id, settings.runtime_dir))
    payload["log"] = _tail(log_path(settings, challenge_id))
    return payload


def stop(settings: AgentSettings, challenge_id: str) -> dict[str, Any]:
    path = request_stop(challenge_id, settings.runtime_dir)
    return {"challenge": challenge_id, "stop_file": str(path)}


#: Children this server started, kept only so they can be waited on once they exit. A
#: process nobody waits on stays in the table as a zombie, which both leaks an entry per run
#: and makes the pid look alive forever.
_CHILDREN: list[subprocess.Popen[bytes]] = []


def default_spawn(argv: list[str], path: Path) -> int:
    """Start the child detached from this request, with its output going to ``path``.

    ``cwd`` is the directory the package was imported from, so the child can import it
    regardless of where the server was launched. Every path in ``argv`` is absolute for the
    same reason.
    """

    with path.open("ab") as handle:
        process = subprocess.Popen(
            argv,
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            cwd=PACKAGE_ROOT,
        )
    _CHILDREN.append(process)
    return process.pid


def reap() -> None:
    """Collect the children that have finished. Cheap, and called on every status read."""

    for process in list(_CHILDREN):
        if process.poll() is not None:
            _CHILDREN.remove(process)


def _one_of(value: object, allowed: tuple[str, ...], label: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{label} must be one of {', '.join(allowed)}")
    return value


def _clamp_int(value: Any, ceiling: int) -> int:
    if value is None:
        return ceiling
    try:
        wanted = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("max_turns must be a whole number") from exc
    if wanted < 1:
        raise ValueError("max_turns must be at least 1")
    return min(wanted, ceiling)


def _clamp_float(value: Any, ceiling: float) -> float:
    if value is None:
        return ceiling
    try:
        wanted = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("max_cost_usd must be a number") from exc
    if wanted <= 0:
        raise ValueError("max_cost_usd must be greater than 0")
    return min(wanted, ceiling)


def _tail(path: Path) -> str:
    if not path.exists():
        return ""
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - LOG_TAIL_BYTES))
        return handle.read().decode("utf-8", errors="replace")
