"""Durable, explicit retry queue for command results that could not be ingested.

The queue is written before an HTTP ingest attempt.  This prioritizes retaining an operator's
command output over convenience: replay is always explicit because a broken connection can
leave the client unable to know whether the server received the original request.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .models import CommandResult


@dataclass(frozen=True)
class PendingCapture:
    id: str
    created_at: str
    challenge: str
    actor: str
    result: CommandResult

    def summary(self) -> dict[str, object]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "challenge": self.challenge,
            "command": self.result["command"],
            "exit_code": self.result["exit_code"],
        }


class PendingCaptureStore:
    """A small local queue; only the Human Client owns this non-secret runtime file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def enqueue(self, *, challenge: str, actor: str, result: CommandResult) -> PendingCapture:
        pending = PendingCapture(
            id=str(uuid4()),
            created_at=datetime.now(UTC).isoformat(),
            challenge=challenge,
            actor=actor,
            result=result,
        )
        self._write([*self.load(), pending])
        return pending

    def load(self) -> list[PendingCapture]:
        try:
            raw: Any = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read pending capture queue {self.path}: {exc}") from exc
        if not isinstance(raw, list):
            raise ValueError(f"pending capture queue {self.path} must contain a JSON list")
        try:
            return [PendingCapture(**item) for item in raw if isinstance(item, dict)]
        except (TypeError, KeyError) as exc:
            raise ValueError(f"pending capture queue {self.path} has an invalid entry") from exc

    def get(self, pending_id: str) -> PendingCapture:
        for pending in self.load():
            if pending.id == pending_id:
                return pending
        raise ValueError(f"pending capture {pending_id!r} was not found")

    def remove(self, pending_id: str) -> None:
        pending = self.load()
        retained = [entry for entry in pending if entry.id != pending_id]
        if len(retained) == len(pending):
            raise ValueError(f"pending capture {pending_id!r} was not found")
        self._write(retained)

    def _write(self, entries: list[PendingCapture]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(
            json.dumps([asdict(entry) for entry in entries], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)
