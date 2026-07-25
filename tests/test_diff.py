"""Semantic diff computation."""

from __future__ import annotations

from memory_core.diff import compute_diff
from memory_core.models import (
    AssumptionItem,
    ChallengeMeta,
    FactItem,
    GuessingState,
    MemorySnapshot,
)


def _snapshot(
    facts: dict[str, list[FactItem]] | None = None,
    assumptions: dict[str, list[AssumptionItem]] | None = None,
    template: str = "generic",
    version: int = 1,
) -> MemorySnapshot:
    return MemorySnapshot(
        challenge=ChallengeMeta(
            id="demo", title="Demo", template=template, template_version=version
        ),
        fact=facts or {"general": []},
        assumption=assumptions or {"general": []},
        guessing=GuessingState(catalog=f"{template}@{version}"),
    )


def test_empty_diff() -> None:
    diff = compute_diff(_snapshot(), _snapshot())
    assert diff.is_empty()


def test_additions_are_reported() -> None:
    after = _snapshot(
        facts={"general": [FactItem(id="F014", text="TCP/80 is open")]},
        assumptions={"general": [AssumptionItem(id="A009", text="nginx reverse proxy?")]},
    )
    diff = compute_diff(_snapshot(), after)
    assert diff.fact_added == ["F014"]
    assert diff.assumption_added == ["A009"]
    assert diff.fact_updated == [] and diff.fact_removed == []


def test_removals_are_reported() -> None:
    before = _snapshot(facts={"general": [FactItem(id="F001", text="x")]})
    diff = compute_diff(before, _snapshot())
    assert diff.fact_removed == ["F001"]


def test_text_change_is_an_update() -> None:
    before = _snapshot(facts={"general": [FactItem(id="F001", text="x")]})
    after = _snapshot(facts={"general": [FactItem(id="F001", text="y")]})
    assert compute_diff(before, after).fact_updated == ["F001"]


def test_section_change_is_an_update_not_a_move_out_and_in() -> None:
    before = _snapshot(facts={"general": [FactItem(id="F001", text="x")], "progress": []})
    after = _snapshot(facts={"general": [], "progress": [FactItem(id="F001", text="x")]})
    diff = compute_diff(before, after)
    assert diff.fact_updated == ["F001"]
    assert diff.fact_added == [] and diff.fact_removed == []


def test_evidence_change_is_an_update() -> None:
    before = _snapshot(facts={"general": [FactItem(id="F001", text="x")]})
    after = _snapshot(facts={"general": [FactItem(id="F001", text="x", evidence=["I-1"])]})
    assert compute_diff(before, after).fact_updated == ["F001"]


def test_promotion_is_reported_under_the_old_id() -> None:
    """Crossing layers mints a new id; the old one is what `promoted` names.

    The old id must not also show up as a removal — the claim did not go away, it changed
    epistemic status.
    """

    before = _snapshot(assumptions={"general": [AssumptionItem(id="A017", text="maybe")]})
    after = _snapshot(facts={"general": [FactItem(id="F002", text="maybe", promoted_from="A017")]})
    diff = compute_diff(before, after)
    assert diff.promoted == ["A017"]
    assert diff.fact_added == ["F002"]
    assert diff.assumption_removed == []


def test_demotion_is_reported_under_the_old_id() -> None:
    before = _snapshot(facts={"general": [FactItem(id="F002", text="was sure")]})
    after = _snapshot(
        assumptions={"general": [AssumptionItem(id="A003", text="was sure", promoted_from="F002")]}
    )
    diff = compute_diff(before, after)
    assert diff.demoted == ["F002"]
    assert diff.assumption_added == ["A003"]
    assert diff.fact_removed == []


def test_a_new_item_merely_citing_an_ancestor_is_not_a_promotion() -> None:
    """`promoted_from` only means a promotion when the ancestor actually left its layer."""

    before = _snapshot(assumptions={"general": [AssumptionItem(id="A017", text="maybe")]})
    after = _snapshot(
        facts={"general": [FactItem(id="F002", text="maybe", promoted_from="A017")]},
        assumptions={"general": [AssumptionItem(id="A017", text="maybe")]},
    )
    diff = compute_diff(before, after)
    assert diff.promoted == []
    assert diff.fact_added == ["F002"]


def test_template_change_is_reported() -> None:
    diff = compute_diff(_snapshot(), _snapshot(template="web", version=1))
    assert diff.template_changed is not None
    assert diff.template_changed.from_ == "generic@1"
    assert diff.template_changed.to == "web@1"


def test_template_change_serializes_with_the_from_alias() -> None:
    """`from` is a Python keyword; the wire format must still say `from`."""

    diff = compute_diff(_snapshot(), _snapshot(template="web"))
    payload = diff.model_dump(mode="json", by_alias=True)
    assert payload["template_changed"] == {"from": "generic@1", "to": "web@1"}


def test_diff_field_names_match_the_frozen_contract() -> None:
    diff = compute_diff(_snapshot(), _snapshot())
    assert set(diff.model_dump().keys()) == {
        "fact_added",
        "fact_updated",
        "fact_removed",
        "assumption_added",
        "assumption_updated",
        "assumption_removed",
        "promoted",
        "demoted",
        "template_changed",
    }
