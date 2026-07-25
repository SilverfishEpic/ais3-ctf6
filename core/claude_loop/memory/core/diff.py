"""Semantic diff between two snapshots.

Every state-changing path — ingest, mutation, reclassification — funnels through
:func:`compute_diff`, so all three produce an identical diff shape.  That is what lets
the Human Client render one thing, and lets the Agent Bridge treat the diff as loop
telemetry without special-casing the operation that produced it.

The diff is *semantic*, not textual: it reports what happened to identities and to
epistemic levels, never a YAML patch.
"""

from __future__ import annotations

from collections.abc import Sequence

from .models import MemoryDiff, MemoryItem, MemorySnapshot, TemplateChange


def _index(pairs: Sequence[tuple[str, MemoryItem]]) -> dict[str, tuple[str, MemoryItem]]:
    return {item.id: (section, item) for section, item in pairs}


def _changed(before: tuple[str, MemoryItem], after: tuple[str, MemoryItem]) -> bool:
    before_section, before_item = before
    after_section, after_item = after
    return (
        before_section != after_section
        or before_item.text != after_item.text
        or before_item.evidence != after_item.evidence
        or before_item.tags != after_item.tags
    )


def _crossed(
    arrivals: dict[str, tuple[str, MemoryItem]],
    departed_from: dict[str, tuple[str, MemoryItem]],
    still_present: set[str],
) -> list[str]:
    """Find items that changed layer, reported under their **previous** id.

    Crossing layers mints a new id, so a promotion is recognised by an arrival in the
    target layer whose ``promoted_from`` names an item that used to be in the source layer
    and is no longer anywhere.  Reporting the old id keeps ``promoted`` meaningful to a
    client holding a stale reference; the new id shows up in ``fact_added`` alongside it.
    """

    return sorted(
        {
            item.promoted_from
            for _, item in arrivals.values()
            if item.promoted_from
            and item.promoted_from in departed_from
            and item.promoted_from not in still_present
        }
    )


def compute_diff(before: MemorySnapshot, after: MemorySnapshot) -> MemoryDiff:
    """Compare two snapshots.

    Promotion and demotion are reported under the item's **old** id, while the new id
    appears as an ordinary addition in the target layer.  The old id is deliberately kept
    out of ``*_removed``: the claim did not go away, it changed epistemic status, and a
    client that renders removals should not show it as deleted.
    """

    before_facts = _index(before.iter_facts())
    after_facts = _index(after.iter_facts())
    before_assumptions = _index(before.iter_assumptions())
    after_assumptions = _index(after.iter_assumptions())

    surviving = set(after_facts) | set(after_assumptions)
    promoted = _crossed(after_facts, before_assumptions, surviving)
    demoted = _crossed(after_assumptions, before_facts, surviving)

    fact_added = sorted(set(after_facts) - set(before_facts))
    fact_removed = sorted(set(before_facts) - set(after_facts) - set(demoted))
    fact_updated = sorted(
        item_id
        for item_id in set(before_facts) & set(after_facts)
        if _changed(before_facts[item_id], after_facts[item_id])
    )

    assumption_added = sorted(set(after_assumptions) - set(before_assumptions))
    assumption_removed = sorted(set(before_assumptions) - set(after_assumptions) - set(promoted))
    assumption_updated = sorted(
        item_id
        for item_id in set(before_assumptions) & set(after_assumptions)
        if _changed(before_assumptions[item_id], after_assumptions[item_id])
    )

    template_changed: TemplateChange | None = None
    before_catalog = f"{before.challenge.template}@{before.challenge.template_version}"
    after_catalog = f"{after.challenge.template}@{after.challenge.template_version}"
    if before_catalog != after_catalog:
        template_changed = TemplateChange(**{"from": before_catalog, "to": after_catalog})

    return MemoryDiff(
        fact_added=fact_added,
        fact_updated=fact_updated,
        fact_removed=fact_removed,
        assumption_added=assumption_added,
        assumption_updated=assumption_updated,
        assumption_removed=assumption_removed,
        promoted=promoted,
        demoted=demoted,
        template_changed=template_changed,
    )
