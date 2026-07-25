"""Controlled human mutation, attribution and override semantics."""

from __future__ import annotations

import pytest
from conftest import human_note
from pydantic import ValidationError

from memory_core.errors import InvalidRequest, RevisionConflict
from memory_core.models import IngestEnvelope, MemoryMutation
from memory_core.service import MemoryService


def _seeded(service: MemoryService, challenge: str) -> int:
    """Ingest one note so F001 (fact) and A001 (assumption) exist; return the revision."""

    service.ingest(challenge, IngestEnvelope.model_validate(human_note("port 80 is open")))
    return service.read_memory(challenge).revision


def test_reason_is_mandatory() -> None:
    """A promotion without a stated reason is not a valid request at all."""

    with pytest.raises(ValidationError):
        MemoryMutation(
            expected_revision=0,
            actor="ctshen",
            reason="",
            operations=[{"op": "promote", "id": "A001"}],
        )


def test_at_least_one_operation_is_required() -> None:
    with pytest.raises(ValidationError):
        MemoryMutation(expected_revision=0, actor="ctshen", reason="because", operations=[])


def test_human_promotion_preserves_the_id_and_records_actor_and_reason(
    service: MemoryService, demo: str
) -> None:
    revision = _seeded(service, demo)

    envelope = service.mutate(
        demo,
        MemoryMutation(
            expected_revision=revision,
            actor="ctshen",
            reason="Verified manually in the browser",
            operations=[{"op": "promote", "id": "A001", "to": "fact"}],
        ),
    )

    assert envelope.diff.promoted == ["A001"]
    # A promoted item is *only* promoted: never an add on one side and a remove on the other.
    assert envelope.diff.fact_added == []
    assert envelope.diff.assumption_removed == []

    memory = service.read_memory(demo)
    promoted = [i for items in memory.fact.values() for i in items if i.id == "A001"]
    assert len(promoted) == 1
    assert all(i.id != "A001" for items in memory.assumption.values() for i in items)

    record = [r for r in service.journal.records(demo) if r.type == "mutation"][-1]
    assert record.actor == "ctshen"
    assert record.reason == "Verified manually in the browser"
    assert record.operations == [{"op": "promote", "id": "A001", "to": "fact"}]
    # The override itself becomes citable evidence for the item it changed.
    assert record.id in promoted[0].evidence


def test_human_override_does_not_need_new_evidence(service: MemoryService, demo: str) -> None:
    """Unlike the model, a human may promote on their own authority — with a reason."""

    revision = _seeded(service, demo)
    envelope = service.mutate(
        demo,
        MemoryMutation(
            expected_revision=revision,
            actor="ctshen",
            reason="I saw it myself",
            operations=[{"op": "promote", "id": "A001"}],
        ),
    )
    assert envelope.diff.promoted == ["A001"]


def test_demotion_moves_a_fact_back_without_deleting_it(service: MemoryService, demo: str) -> None:
    revision = _seeded(service, demo)
    envelope = service.mutate(
        demo,
        MemoryMutation(
            expected_revision=revision,
            actor="ctshen",
            reason="the scan was against the wrong host",
            operations=[{"op": "demote", "id": "F001"}],
        ),
    )
    assert envelope.diff.demoted == ["F001"]
    assert envelope.diff.fact_removed == []
    memory = service.read_memory(demo)
    assert any(i.id == "F001" for items in memory.assumption.values() for i in items)


def test_move_changes_section_only(service: MemoryService, demo: str) -> None:
    revision = _seeded(service, demo)
    envelope = service.mutate(
        demo,
        MemoryMutation(
            expected_revision=revision,
            actor="ctshen",
            reason="better placement",
            operations=[{"op": "move", "id": "F001", "section": "progress"}],
        ),
    )
    assert envelope.diff.fact_updated == ["F001"]
    assert envelope.diff.promoted == [] and envelope.diff.demoted == []
    memory = service.read_memory(demo)
    assert [i.id for i in memory.fact["progress"]] == ["F001"]


def test_remove_deletes_an_item(service: MemoryService, demo: str) -> None:
    revision = _seeded(service, demo)
    envelope = service.mutate(
        demo,
        MemoryMutation(
            expected_revision=revision,
            actor="ctshen",
            reason="duplicate",
            operations=[{"op": "remove", "id": "A001"}],
        ),
    )
    assert envelope.diff.assumption_removed == ["A001"]


def test_unknown_id_is_rejected(service: MemoryService, demo: str) -> None:
    with pytest.raises(InvalidRequest):
        service.mutate(
            demo,
            MemoryMutation(
                expected_revision=0,
                actor="ctshen",
                reason="oops",
                operations=[{"op": "remove", "id": "F999"}],
            ),
        )


def test_promoting_something_that_is_already_a_fact_is_rejected(
    service: MemoryService, demo: str
) -> None:
    revision = _seeded(service, demo)
    with pytest.raises(InvalidRequest):
        service.mutate(
            demo,
            MemoryMutation(
                expected_revision=revision,
                actor="ctshen",
                reason="double promote",
                operations=[{"op": "promote", "id": "F001"}],
            ),
        )


def test_stale_expected_revision_returns_conflict(service: MemoryService, demo: str) -> None:
    revision = _seeded(service, demo)
    with pytest.raises(RevisionConflict) as excinfo:
        service.mutate(
            demo,
            MemoryMutation(
                expected_revision=revision - 1,
                actor="ctshen",
                reason="stale client",
                operations=[{"op": "promote", "id": "A001"}],
            ),
        )
    assert excinfo.value.status == 409
    assert excinfo.value.details["current_revision"] == revision


def test_rejected_mutation_leaves_no_phantom_journal_record(
    service: MemoryService, demo: str
) -> None:
    """A 409 must not leave an override in the evidence log that never happened."""

    revision = _seeded(service, demo)
    before = len([r for r in service.journal.records(demo) if r.type == "mutation"])
    with pytest.raises(RevisionConflict):
        service.mutate(
            demo,
            MemoryMutation(
                expected_revision=revision - 1,
                actor="ctshen",
                reason="stale",
                operations=[{"op": "promote", "id": "A001"}],
            ),
        )
    after = len([r for r in service.journal.records(demo) if r.type == "mutation"])
    assert before == after


def test_there_is_no_arbitrary_patch_operation() -> None:
    with pytest.raises(ValidationError):
        MemoryMutation(
            expected_revision=0,
            actor="ctshen",
            reason="sneaky",
            operations=[{"op": "patch", "path": "/challenge/summary", "value": "anything"}],
        )
