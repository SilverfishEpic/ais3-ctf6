"""Per-challenge locking for the single-process MVP.

Two rules matter more than the mechanism:

1. **Memory Core is the only writer of ``state.yaml``.**  CLI, TUI, Claude skill and
   Agent Bridge all go through the API.
2. **No lock is ever held across an LLM call.**  Ingest reads a snapshot, releases,
   summarizes, then re-acquires for a short commit critical section.  That is what keeps
   a human ``mutate`` responsive while an agent run is being summarized.

The MVP deliberately promises only a single Memory Core process (``--workers 1``).  A
distributed lock is an explicit non-goal; if multi-process ever becomes a real
requirement it needs an ADR, not a quiet dependency.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager


class LockRegistry:
    """Hands out one re-entrant lock per challenge ID."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, threading.RLock] = {}

    def get(self, challenge_id: str) -> threading.RLock:
        with self._guard:
            lock = self._locks.get(challenge_id)
            if lock is None:
                lock = threading.RLock()
                self._locks[challenge_id] = lock
            return lock

    @contextmanager
    def hold(self, challenge_id: str) -> Iterator[None]:
        lock = self.get(challenge_id)
        lock.acquire()
        try:
            yield
        finally:
            lock.release()
