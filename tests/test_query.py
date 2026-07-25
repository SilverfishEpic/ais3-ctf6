"""Natural-language query: grounded in current state, references verified."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from conftest import TEMPLATES_DIR, human_note

from memory_core.models import (
    AnswerProposal,
    CreateChallengeRequest,
    IngestEnvelope,
    MemorySnapshot,
    QueryRequest,
)
from memory_core.service import MemoryService
from memory_core.summarizer import FakeSummarizer


def test_query_references_only_existing_memory_ids(service: MemoryService, demo: str) -> None:
    service.ingest(demo, IngestEnvelope.model_validate(human_note("port 80 is open")))
    response = service.query(demo, QueryRequest(question="which ports are open?"))

    existing = {i.id for _, i in service.store.load(demo).iter_facts()} | {
        i.id for _, i in service.store.load(demo).iter_assumptions()
    }
    assert response.references
    assert set(response.references) <= existing
    assert response.revision == service.read_memory(demo).revision


class HallucinatingSummarizer(FakeSummarizer):
    """Returns a plausible-looking ID that does not exist."""

    def answer(
        self, *, state: MemorySnapshot, question: str, layers: Sequence[str]
    ) -> AnswerProposal:
        return AnswerProposal(
            answer="Port 80 and port 443 are both confirmed open.",
            references=["F001", "F999", "A404"],
        )


def test_invented_references_are_dropped_before_reaching_the_client(runs_dir: Path) -> None:
    """The model may phrase the answer; it may not introduce content."""

    service = MemoryService.build(
        runs_dir=runs_dir, templates_dir=TEMPLATES_DIR, summarizer=HallucinatingSummarizer()
    )
    service.create_challenge(CreateChallengeRequest(id="c", title="C"))
    service.ingest("c", IngestEnvelope.model_validate(human_note("port 80 is open")))

    response = service.query("c", QueryRequest(question="what is open?"))
    assert response.references == ["F001"]


def test_query_on_an_empty_whiteboard_abstains(service: MemoryService, demo: str) -> None:
    response = service.query(demo, QueryRequest(question="what do we know?"))
    assert response.references == []
    assert "Nothing" in response.answer


def test_query_respects_the_layer_filter(service: MemoryService, demo: str) -> None:
    service.ingest(demo, IngestEnvelope.model_validate(human_note("port 80 is open")))
    facts_only = service.query(demo, QueryRequest(question="what is confirmed?", layers=["fact"]))
    assert all(ref.startswith("F") for ref in facts_only.references)


def test_query_does_not_change_state(service: MemoryService, demo: str) -> None:
    service.ingest(demo, IngestEnvelope.model_validate(human_note("port 80 is open")))
    before = service.read_memory(demo).revision
    service.query(demo, QueryRequest(question="anything?"))
    assert service.read_memory(demo).revision == before
