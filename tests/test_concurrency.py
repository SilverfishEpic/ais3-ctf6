"""Concurrency: no silent lost updates, and no lock held across an LLM call.

The scenario this protects is the real one: a human corrects the whiteboard while an
external agent's run log is being summarized.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from pathlib import Path

import pytest
from tests.conftest import TEMPLATES_DIR, human_note

from memory.core.errors import RevisionConflict
from memory.core.models import (
    ClassificationProposal,
    CreateChallengeRequest,
    IngestEnvelope,
    MemoryMutation,
    MemorySnapshot,
    ProposedItem,
    SourceRecord,
    SummaryProposal,
    TemplateDefinition,
)
from memory.core.service import MemoryService
from memory.core.summarizer import FakeSummarizer


class SlowSummarizer(FakeSummarizer):
    """A Summarizer that blocks until released, so we can interleave a human write."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def summarize(
        self,
        *,
        state: MemorySnapshot,
        source_record: SourceRecord,
        template: TemplateDefinition,
    ) -> SummaryProposal:
        self.calls += 1
        self.entered.set()
        self.release.wait(timeout=5)
        return super().summarize(state=state, source_record=source_record, template=template)


def test_two_clients_on_the_same_revision_cannot_both_win(
    service: MemoryService, demo: str
) -> None:
    service.ingest(demo, IngestEnvelope.model_validate(human_note("port 80 is open")))
    revision = service.read_memory(demo).revision

    first = MemoryMutation(
        expected_revision=revision,
        actor="alice",
        reason="promote it",
        operations=[{"op": "promote", "id": "A001"}],
    )
    second = MemoryMutation(
        expected_revision=revision,
        actor="bob",
        reason="remove it",
        operations=[{"op": "remove", "id": "A001"}],
    )

    service.mutate(demo, first)
    with pytest.raises(RevisionConflict):
        service.mutate(demo, second)

    # Alice's change stands; Bob was told to re-read rather than silently overwriting it.
    memory = service.read_memory(demo)
    assert any(i.promoted_from == "A001" for items in memory.fact.values() for i in items)
    assert memory.revision == revision + 1


def test_human_mutation_is_not_blocked_by_an_in_flight_summarizer(runs_dir: Path) -> None:
    """The core concurrency claim: no lock is held during the LLM call.

    If ingest held the challenge lock across ``summarize``, the human mutation below
    would block until the summarizer was released and this test would time out.
    """

    summarizer = SlowSummarizer()
    service = MemoryService.build(
        runs_dir=runs_dir, templates_dir=TEMPLATES_DIR, summarizer=summarizer
    )
    service.create_challenge(CreateChallengeRequest(id="c", title="C"))

    ingest_result: list[object] = []

    def run_ingest() -> None:
        ingest_result.append(
            service.ingest("c", IngestEnvelope.model_validate(human_note("port 80 is open")))
        )

    thread = threading.Thread(target=run_ingest)
    thread.start()
    assert summarizer.entered.wait(timeout=5), "summarizer never started"

    # The summarizer is mid-call. A human write must still go through immediately.
    started = time.monotonic()
    service.mutate(
        "c",
        MemoryMutation(
            expected_revision=0,
            actor="ctshen",
            reason="I already know this is a web app",
            operations=[{"op": "add", "layer": "assumption", "text": "web app"}],
        ),
    )
    assert time.monotonic() - started < 2.0

    summarizer.release.set()
    thread.join(timeout=10)

    # Ingest lost the commit race, re-read at the new revision and re-summarized.
    assert summarizer.calls == 2
    assert len(ingest_result) == 1
    memory = service.read_memory("c")
    assert memory.revision == 2
    texts = {i.text for items in memory.fact.values() for i in items} | {
        i.text for items in memory.assumption.values() for i in items
    }
    assert "web app" in texts  # the human's write survived
    assert "TCP/80 is open." in texts  # and so did the rebased ingest


class AlwaysConflictingSummarizer:
    """Bumps the revision behind ingest's back on every call, to exhaust the retries."""

    def __init__(self, service_ref: list[MemoryService], challenge_id: str) -> None:
        self.service_ref = service_ref
        self.challenge_id = challenge_id
        self.calls = 0

    def summarize(
        self,
        *,
        state: MemorySnapshot,
        source_record: SourceRecord,
        template: TemplateDefinition,
    ) -> SummaryProposal:
        self.calls += 1
        service = self.service_ref[0]
        service.store.commit(self.challenge_id, None, lambda s: s)
        return SummaryProposal(assumption_add=[ProposedItem(text=f"attempt {self.calls}")])

    def classify(
        self, *, initial_input: str, templates: Sequence[TemplateDefinition]
    ) -> ClassificationProposal:
        return ClassificationProposal(template="generic")

    def answer(self, **kwargs: object) -> object:  # pragma: no cover - unused here
        raise NotImplementedError


def test_ingest_gives_up_with_a_conflict_after_bounded_retries(runs_dir: Path) -> None:
    """Rebasing is bounded: we retry, then tell the caller, rather than looping forever."""

    holder: list[MemoryService] = []
    summarizer = AlwaysConflictingSummarizer(holder, "c")
    service = MemoryService.build(
        runs_dir=runs_dir, templates_dir=TEMPLATES_DIR, summarizer=summarizer, max_rebase=2
    )
    holder.append(service)
    service.create_challenge(CreateChallengeRequest(id="c", title="C"))

    with pytest.raises(RevisionConflict):
        service.ingest("c", IngestEnvelope.model_validate(human_note("anything")))

    assert summarizer.calls == 3  # initial attempt + max_rebase retries
    # The input itself was never lost, even though nothing could be committed.
    assert len(service.journal.records("c")) == 1
