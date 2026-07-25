"""Append-only source/evidence record: ``runs/<challenge-id>/journal.jsonl``.

Borrowed from Generative Agents (arXiv:2304.03442): a derived summary must never replace
the raw experience it came from.  Every input is written here *verbatim before* the
Summarizer sees it, so a summarization failure can never lose what the human or agent
actually reported.

What the journal is **not**:

* not a fourth semantic memory layer — Fact/Assumption/Guessing are the only three;
* not an event-sourcing log — state is never rebuilt by replaying it.

What it is for: stable evidence IDs that Fact/Assumption entries can cite, human
override attribution (CHAP), and deterministic replay when comparing Summarizers or
templates on identical inputs.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from .errors import ChallengeNotFound
from .ids import source_id
from .locks import LockRegistry
from .models import IngestEnvelope, MemoryMutation, SourceRecord
from .store import require_safe_challenge_id

JOURNAL_FILENAME = "journal.jsonl"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class Journal:
    """Append-only per-challenge journal."""

    def __init__(self, runs_dir: Path, locks: LockRegistry | None = None) -> None:
        self.runs_dir = Path(runs_dir)
        self.locks = locks or LockRegistry()

    def path(self, challenge_id: str) -> Path:
        return self.runs_dir / require_safe_challenge_id(challenge_id) / JOURNAL_FILENAME

    def ensure(self, challenge_id: str) -> None:
        path = self.path(challenge_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)

    # -- append ------------------------------------------------------------

    def append_input(
        self, challenge_id: str, envelope: IngestEnvelope, *, at: str | None = None
    ) -> SourceRecord:
        # exclude_none so the record mirrors what the client actually sent, rather than
        # what our optional fields defaulted to.
        payload = envelope.input.model_dump(mode="json", exclude_none=True)
        actor = envelope.source.actor or envelope.source.type
        with self.locks.hold(challenge_id):
            seq = self._count(challenge_id)
            record = SourceRecord(
                id=source_id("I", json.dumps(payload, sort_keys=True, ensure_ascii=False), seq),
                type="input",
                at=at or utc_now(),
                actor=actor,
                source=envelope.source,
                input_type=envelope.input.type,
                content=cast(dict[str, object], payload),
            )
            self._append(challenge_id, record)
        return record

    def append_mutation(
        self, challenge_id: str, mutation: MemoryMutation, *, at: str | None = None
    ) -> SourceRecord:
        operations = [op.model_dump(mode="json", exclude_none=True) for op in mutation.operations]
        with self.locks.hold(challenge_id):
            seq = self._count(challenge_id)
            record = SourceRecord(
                id=source_id("M", json.dumps(operations, sort_keys=True, ensure_ascii=False), seq),
                type="mutation",
                at=at or utc_now(),
                actor=mutation.actor,
                reason=mutation.reason,
                operations=cast(list[dict[str, object]], operations),
            )
            self._append(challenge_id, record)
        return record

    def append_template_change(
        self,
        challenge_id: str,
        *,
        actor: str,
        reason: str,
        from_catalog: str,
        to_catalog: str,
        at: str | None = None,
    ) -> SourceRecord:
        """Reclassification is a human decision and is attributed like any other override."""

        operations = [
            {"op": "template", "from": from_catalog, "to": to_catalog},
        ]
        with self.locks.hold(challenge_id):
            seq = self._count(challenge_id)
            record = SourceRecord(
                id=source_id("M", json.dumps(operations, sort_keys=True), seq),
                type="mutation",
                at=at or utc_now(),
                actor=actor,
                reason=reason,
                operations=cast(list[dict[str, object]], operations),
            )
            self._append(challenge_id, record)
        return record

    def rollback_marker(self, challenge_id: str) -> int:
        """Return the current durable end of the journal for all-or-nothing mutations."""

        path = self.path(challenge_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        return path.stat().st_size

    def rollback_to(self, challenge_id: str, marker: int) -> None:
        """Remove records appended after ``marker`` and fsync the truncated journal."""

        path = self.path(challenge_id)
        with open(path, "r+b") as handle:
            handle.truncate(marker)
            handle.flush()
            os.fsync(handle.fileno())

    def _append(self, challenge_id: str, record: SourceRecord) -> None:
        path = self.path(challenge_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        # exclude_none keeps the journal readable and, importantly, byte-identical to what
        # the client actually sent — no synthesized nulls appear in the evidence record.
        line = json.dumps(
            record.model_dump(mode="json", exclude_none=True), ensure_ascii=False, sort_keys=False
        )
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    # -- read --------------------------------------------------------------

    def _count(self, challenge_id: str) -> int:
        path = self.path(challenge_id)
        if not path.is_file():
            return 0
        with open(path, encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())

    def iter_records(self, challenge_id: str) -> Iterator[SourceRecord]:
        path = self.path(challenge_id)
        if not path.is_file():
            raise ChallengeNotFound(
                f"no journal for challenge {challenge_id!r}", challenge_id=challenge_id
            )
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    raw: dict[str, Any] = json.loads(line)
                    yield SourceRecord.model_validate(raw)

    def records(self, challenge_id: str) -> list[SourceRecord]:
        return list(self.iter_records(challenge_id))

    def get(self, challenge_id: str, record_id: str) -> SourceRecord | None:
        for record in self.iter_records(challenge_id):
            if record.id == record_id:
                return record
        return None
