"""Stable identity allocation.

Memory Core — never the Summarizer, never a client — assigns memory IDs.

**An ID is never reused.**  That is a stronger promise than "unique among current items",
and it needs a persisted high-water mark to keep: scanning only the items present in a
snapshot would happily hand out ``F001`` again after the original ``F001`` was removed, or
after it was promoted into the other layer and left a gap behind.  A reused ID means the
same name refers to two different claims across the journal's history, which quietly
destroys the audit trail every evidence reference depends on.

So the counters live in ``meta.fact_seq`` / ``meta.assumption_seq`` and only ever go up.
The scan over live IDs is kept as a floor, so a hand-edited or externally-produced
``state.yaml`` with a stale counter still cannot collide.
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
        match = _ID_RE.match(item_id)
        if match and match.group(1) == prefix:
            best = max(best, int(match.group(2)))
    return best


class IdAllocator:
    """Hands out ``F001`` / ``A001`` style IDs for one commit.

    Construct it from the snapshot being modified and keep it for the whole commit, so
    that several additions in one proposal cannot collide with each other.  Allocation
    advances the snapshot's persisted high-water marks in place.
    """

    def __init__(self, snapshot: MemorySnapshot) -> None:
        self._meta = snapshot.meta
        live = snapshot.all_ids()
        # Also honour ids referenced only as promotion ancestry, so a promoted-away id is
        # never handed out again even if the counters were lost.
        historic = {
            item.promoted_from
            for _, item in [*snapshot.iter_facts(), *snapshot.iter_assumptions()]
            if item.promoted_from
        }
        known = live | historic
        self._meta.fact_seq = max(self._meta.fact_seq, _max_number("F", known))
        self._meta.assumption_seq = max(self._meta.assumption_seq, _max_number("A", known))

    def next_fact_id(self) -> str:
        return self._next("F")

    def next_assumption_id(self) -> str:
        return self._next("A")

    def next_for_layer(self, layer: str) -> str:
        return self._next("F" if layer == "fact" else "A")

    def _next(self, prefix: str) -> str:
        if prefix == "F":
            self._meta.fact_seq += 1
            return f"F{self._meta.fact_seq:03d}"
        self._meta.assumption_seq += 1
        return f"A{self._meta.assumption_seq:03d}"


def source_id(prefix: str, payload: str, sequence: int) -> str:
    """Deterministic journal ID.

    Deriving the ID from ``(payload, sequence)`` rather than from a random source keeps
    journal replay — the mechanism the ablation harness uses to compare Summarizers on
    identical inputs — reproducible.
    """

    digest = hashlib.sha256(f"{sequence}\x00{payload}".encode()).hexdigest()[:8]
    return f"{prefix}-{digest}"
