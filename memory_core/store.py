"""Persistence for ``runs/<challenge-id>/state.yaml``.

Durability strategy: write a temp file in the same directory, ``fsync`` it, ``os.replace``
it over the target, then ``fsync`` the directory.  That gives us crash-atomicity —
a reader never observes a half-written file.

It does **not** give us logical safety.  Atomic rename cannot prevent a lost update: two
callers that both read revision 41 and both write revision 42 each produce a perfectly
valid file, and one of them silently wins.  The protection against that is the revision
check performed *inside* the commit critical section (:meth:`StateStore.commit`), which
is why both mechanisms exist.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .errors import ChallengeExists, ChallengeNotFound, RevisionConflict, StateCorrupt
from .locks import LockRegistry
from .models import MemorySnapshot

STATE_FILENAME = "state.yaml"


def _prune_none(value: Any) -> Any:
    """Drop ``None`` values so ``state.yaml`` stays readable for humans.

    Safe because every optional field in :class:`MemorySnapshot` has a default, so a
    pruned document round-trips to an identical model.
    """

    if isinstance(value, dict):
        return {k: _prune_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_prune_none(v) for v in value]
    return value


class StateStore:
    """Loads, validates and atomically persists challenge state."""

    def __init__(self, runs_dir: Path, locks: LockRegistry | None = None) -> None:
        self.runs_dir = Path(runs_dir)
        self.locks = locks or LockRegistry()

    # -- paths -------------------------------------------------------------

    def challenge_dir(self, challenge_id: str) -> Path:
        return self.runs_dir / challenge_id

    def state_path(self, challenge_id: str) -> Path:
        return self.challenge_dir(challenge_id) / STATE_FILENAME

    def exists(self, challenge_id: str) -> bool:
        return self.state_path(challenge_id).is_file()

    def list_challenges(self) -> list[str]:
        if not self.runs_dir.is_dir():
            return []
        return sorted(p.name for p in self.runs_dir.iterdir() if (p / STATE_FILENAME).is_file())

    # -- read / write ------------------------------------------------------

    def init(self, snapshot: MemorySnapshot) -> MemorySnapshot:
        challenge_id = snapshot.challenge.id
        with self.locks.hold(challenge_id):
            if self.exists(challenge_id):
                raise ChallengeExists(
                    f"challenge {challenge_id!r} already exists", challenge_id=challenge_id
                )
            self.challenge_dir(challenge_id).mkdir(parents=True, exist_ok=True)
            self._write(challenge_id, snapshot)
        return snapshot

    def load(self, challenge_id: str) -> MemorySnapshot:
        path = self.state_path(challenge_id)
        if not path.is_file():
            raise ChallengeNotFound(
                f"challenge {challenge_id!r} not found", challenge_id=challenge_id
            )
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise StateCorrupt(f"state.yaml for {challenge_id!r} is not valid YAML: {exc}") from exc
        try:
            return MemorySnapshot.model_validate(raw)
        except ValidationError as exc:
            raise StateCorrupt(
                f"state.yaml for {challenge_id!r} does not match the v2 schema",
                errors=exc.errors(include_url=False),
            ) from exc

    def _write(self, challenge_id: str, snapshot: MemorySnapshot) -> None:
        directory = self.challenge_dir(challenge_id)
        directory.mkdir(parents=True, exist_ok=True)
        payload = _prune_none(snapshot.model_dump(mode="json"))
        text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100)

        fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=".state-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.state_path(challenge_id))
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        _fsync_dir(directory)

    # -- commit critical section -------------------------------------------

    @contextmanager
    def hold(self, challenge_id: str) -> Iterator[None]:
        with self.locks.hold(challenge_id):
            yield

    def commit(
        self,
        challenge_id: str,
        expected_revision: int | None,
        apply: Callable[[MemorySnapshot], MemorySnapshot],
    ) -> tuple[MemorySnapshot, MemorySnapshot]:
        """Short critical section: re-read, verify revision, apply, bump, atomic-write.

        ``expected_revision=None`` means "whatever is current" and is used only by paths
        that have no optimistic-concurrency contract of their own.  Every client-facing
        mutation passes a concrete revision.

        Returns ``(before, after)`` so callers can compute a semantic diff.
        """

        with self.locks.hold(challenge_id):
            before = self.load(challenge_id)
            if expected_revision is not None and before.revision != expected_revision:
                raise RevisionConflict(
                    "state has moved on since the request was built",
                    expected_revision=expected_revision,
                    current_revision=before.revision,
                )
            after = apply(before.model_copy(deep=True))
            after.revision = before.revision + 1
            self._write(challenge_id, after)
            return before, after


def _fsync_dir(directory: Path) -> None:
    """Persist the rename itself, not just the file contents."""

    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:  # pragma: no cover - platform dependent
        return
    try:
        os.fsync(fd)
    except OSError:  # pragma: no cover - some filesystems refuse this
        pass
    finally:
        os.close(fd)
