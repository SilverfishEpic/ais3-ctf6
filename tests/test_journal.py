"""Journal: raw inputs survive verbatim, IDs are stable, records are append-only."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import agent_run_log, human_note

from memory_core.journal import Journal
from memory_core.models import CreateChallengeRequest, IngestEnvelope
from memory_core.service import MemoryService


def test_human_note_is_preserved_verbatim(journal: Journal, runs_dir: Path) -> None:
    journal.ensure("demo")
    text = "I manually tested the login flow and the cookie is not HttpOnly."
    record = journal.append_input("demo", IngestEnvelope.model_validate(human_note(text)))

    assert record.id.startswith("I-")
    stored = json.loads((runs_dir / "demo" / "journal.jsonl").read_text(encoding="utf-8"))
    assert stored["content"]["content"] == text
    assert stored["actor"] == "ctshen"
    assert stored["source"]["type"] == "human"


def test_normalized_agent_run_log_is_preserved(journal: Journal) -> None:
    journal.ensure("demo")
    events = [
        {"seq": 1, "role": "assistant", "kind": "message", "content": "let me check the binary"},
        {"seq": 2, "role": "assistant", "kind": "tool_call", "name": "Bash", "content": "checksec"},
        {"seq": 3, "role": "tool", "kind": "tool_result", "content": "NX enabled"},
    ]
    record = journal.append_input("demo", IngestEnvelope.model_validate(agent_run_log(events)))

    reloaded = journal.get("demo", record.id)
    assert reloaded is not None
    assert reloaded.input_type == "run_log"
    assert reloaded.content is not None
    assert reloaded.content["events"] == events
    # Provider metadata is retained without becoming part of the domain model.
    assert reloaded.source is not None
    assert (reloaded.source.provider, reloaded.source.run_id) == ("anthropic", "run-123")


def test_journal_is_append_only_and_ids_are_unique(journal: Journal) -> None:
    journal.ensure("demo")
    ids = [
        journal.append_input("demo", IngestEnvelope.model_validate(human_note(f"note {i}"))).id
        for i in range(5)
    ]
    assert len(set(ids)) == 5
    assert [r.id for r in journal.records("demo")] == ids


def test_identical_inputs_get_distinct_ids(journal: Journal) -> None:
    """Same text twice is two observations, not one — the sequence disambiguates them."""

    journal.ensure("demo")
    first = journal.append_input("demo", IngestEnvelope.model_validate(human_note("same")))
    second = journal.append_input("demo", IngestEnvelope.model_validate(human_note("same")))
    assert first.id != second.id


def test_ingest_journals_input_even_when_summarizer_finds_nothing(
    service: MemoryService,
) -> None:
    service.create_challenge(CreateChallengeRequest(id="c", title="C"))
    envelope = IngestEnvelope.model_validate(human_note("   "))
    response = service.ingest("c", envelope)

    assert response.revision_after == response.revision_before  # no-op, no revision burn
    assert service.journal.get("c", response.source_id) is not None


def test_initial_input_is_journalled_at_creation(service: MemoryService) -> None:
    service.create_challenge(
        CreateChallengeRequest(id="c", title="C", initial_input="A login page over HTTP.")
    )
    records = service.journal.records("c")
    assert [r.input_type for r in records] == ["note"]
    assert records[0].content is not None
    assert records[0].content["content"] == "A login page over HTTP."
    # ...but creation never summarizes, so the whiteboard is still empty at revision 0.
    assert service.read_memory("c").revision == 0
