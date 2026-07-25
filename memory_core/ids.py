"""Stable identity allocation.

Memory Core — never the Summarizer, never a client — assigns memory IDs.  IDs are
allocated from the high-water mark of *all* IDs currently present in a snapshot, so an
ID is never reused even after its item is removed within the same revision batch.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

from .models import MemorySnapshot

_ID_RE = re.compile(r"^([FA])(\d+)$")


def _max_number(prefix: str, ids: Iterable[str]) -> int:
    best = 0
    for item_id in ids:
        m = _ID_RE.match(item_id)
        if m and m.group(1) == prefix:
            best = max(best, int(m.group(2)))
    return best


class IdAllocator:
    """Hands out ``F001`` / ``A001`` style IDs for one commit.

    Construct it from the snapshot being modified and keep it for the whole commit so
    that several additions in a single proposal cannot collide with each other.
    """

    def __init__(self, snapshot: MemorySnapshot) -> None:
        existing = snapshot.all_ids()
        self._counters = {
            "F": _max_number("F", existing),
            "A": _max_number("A", existing),
        }

    def next_fact_id(self) -> str:
        return self._next("F")

    def next_assumption_id(self) -> str:
        return self._next("A")

    def next_for_layer(self, layer: str) -> str:
        return self._next("F" if layer == "fact" else "A")

    def _next(self, prefix: str) -> str:
        self._counters[prefix] += 1
        return f"{prefix}{self._counters[prefix]:03d}"


def source_id(prefix: str, payload: str, sequence: int) -> str:
    """Deterministic journal ID.

    Deriving the ID from ``(payload, sequence)`` rather than from a random source keeps
    journal replay — the mechanism the ablation harness uses to compare Summarizers on
    identical inputs — reproducible.
    """

    digest = hashlib.sha256(f"{sequence}\x00{payload}".encode()).hexdigest()[:8]
    return f"{prefix}-{digest}"
