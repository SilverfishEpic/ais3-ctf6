"""Local runtime files for `ctfmem agent`.

These files are client-owned control metadata.  They intentionally live outside the
Memory Core `runs/` directory so the CLI never touches `state.yaml` or `journal.jsonl`.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from agent_bridge.loop import StopSignal

DEFAULT_RUNTIME_DIR = ".ctfmem"


@dataclass
class AgentStatus:
    challenge: str
    mode: str
    session: str
    status: str
    started_at: str
    updated_at: str
    pid: int
    turns: int = 0
    productive_turns: int = 0
    last_revision: int | None = None
    stopped_because: str | None = None
    total_cost_usd: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def runtime_dir(challenge_id: str, root: str | Path | None = None) -> Path:
    return Path(root or DEFAULT_RUNTIME_DIR) / challenge_id


def stop_file(challenge_id: str, root: str | Path | None = None) -> Path:
    return runtime_dir(challenge_id, root) / "agent.stop"


def status_file(challenge_id: str, root: str | Path | None = None) -> Path:
    return runtime_dir(challenge_id, root) / "agent-status.json"


def make_stop_signal(challenge_id: str, root: str | Path | None = None) -> StopSignal:
    return StopSignal(stop_file(challenge_id, root))


def request_stop(challenge_id: str, root: str | Path | None = None) -> Path:
    path = stop_file(challenge_id, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(utc_now() + "\n", encoding="utf-8")
    return path


def clear_stop(challenge_id: str, root: str | Path | None = None) -> None:
    path = stop_file(challenge_id, root)
    if path.exists():
        path.unlink()


def write_status(status: AgentStatus, root: str | Path | None = None) -> Path:
    path = status_file(status.challenge, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(status.to_dict(), ensure_ascii=False, indent=2) + "\n"
    path.write_text(payload, encoding="utf-8")
    return path


def read_status(challenge_id: str, root: str | Path | None = None) -> dict[str, Any] | None:
    path = status_file(challenge_id, root)
    if not path.exists():
        return None
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def process_is_running(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
