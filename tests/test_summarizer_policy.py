"""The epistemic policy — the rules that stop the whiteboard from lying.

These are the tests that matter most.  A memory system that lets plausible inference
become Fact is worse than no memory system, because downstream everything trusts the
Fact layer.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import TEMPLATES_DIR, agent_run_log, command_result, human_note

from memory_core.errors import SummarizerUnauthorized, SummarizerUnavailable, TemplateNotFound
from memory_core.models import (
    AssumptionItem,
    ChallengeMeta,
    ClassificationProposal,
    CreateChallengeRequest,
    FactItem,
    GuessingState,
    IngestEnvelope,
    MemoryMutation,
    MemorySnapshot,
    ProposedItem,
    ProposedPromotion,
    ProposedUpdate,
    SourceRecord,
    SummaryProposal,
    TemplateDefinition,
)
from memory_core.service import MemoryService
from memory_core.summarizer import FakeSummarizer, enforce_epistemic_policy, is_direct_evidence


def _record(**kwargs: object) -> SourceRecord:
    base: dict[str, object] = {
        "id": "I-aaaa1111",
        "type": "input",
        "at": "2026-07-25T00:00:00Z",
        "actor": "ctshen",
    }
    base.update(kwargs)
    return SourceRecord.model_validate(base)


def _snapshot(assumptions: list[AssumptionItem] | None = None) -> MemorySnapshot:
    return MemorySnapshot(
        challenge=ChallengeMeta(id="demo", title="Demo"),
        fact={"general": []},
        assumption={"general": assumptions or []},
        guessing=GuessingState(catalog="generic@1"),
    )


# -- R2: what counts as direct evidence -------------------------------------


def test_command_result_is_direct_evidence() -> None:
    record = _record(
        input_type="command_result",
        source={"type": "human", "actor": "ctshen"},
        content={"command": ["nmap"], "exit_code": 0, "stdout": "80/tcp open", "stderr": ""},
    )
    assert is_direct_evidence(record) is True


def test_human_note_is_direct_evidence() -> None:
    """A human is a first-party observer; the prompt tells the model to keep inferences
    in the Assumption layer, but the gate admits the human as an evidence source."""

    record = _record(
        input_type="note",
        source={"type": "human", "actor": "ctshen"},
        content={"content": "I ran nmap and port 80 is open."},
    )
    assert is_direct_evidence(record) is True


def test_agent_narrative_is_not_direct_evidence() -> None:
    record = _record(
        input_type="run_log",
        source={"type": "agent", "provider": "anthropic"},
        content={
            "events": [
                {"seq": 1, "role": "assistant", "kind": "message", "content": "probably SQLi"}
            ]
        },
    )
    assert is_direct_evidence(record) is False


def test_agent_tool_result_is_direct_evidence() -> None:
    record = _record(
        input_type="run_log",
        source={"type": "agent", "provider": "anthropic"},
        content={
            "events": [
                {"seq": 1, "role": "assistant", "kind": "message", "content": "checking"},
                {"seq": 2, "role": "tool", "kind": "tool_result", "content": "NX enabled"},
            ]
        },
    )
    assert is_direct_evidence(record) is True


# -- R1/R3: unsupported facts are downgraded, not accepted --------------------


def test_fact_without_direct_evidence_is_downgraded_to_assumption() -> None:
    proposal = SummaryProposal(
        fact_add=[ProposedItem(text="The app is vulnerable to SQLi.", evidence=["I-aaaa1111"])]
    )
    result = enforce_epistemic_policy(
        proposal,
        snapshot=_snapshot(),
        is_direct=lambda _: False,
        known_source_ids={"I-aaaa1111"},
    )
    assert result.proposal.fact_add == []
    assert [i.text for i in result.proposal.assumption_add] == ["The app is vulnerable to SQLi."]
    assert any("downgraded" in note for note in result.notes)


def test_fact_citing_unknown_evidence_is_downgraded() -> None:
    proposal = SummaryProposal(fact_add=[ProposedItem(text="Port 80 open", evidence=["I-ffff"])])
    result = enforce_epistemic_policy(
        proposal, snapshot=_snapshot(), is_direct=lambda _: True, known_source_ids=set()
    )
    assert result.proposal.fact_add == []
    assert len(result.proposal.assumption_add) == 1


def test_fact_with_direct_evidence_survives() -> None:
    proposal = SummaryProposal(
        fact_add=[ProposedItem(text="TCP/80 is open.", evidence=["I-aaaa1111"])]
    )
    result = enforce_epistemic_policy(
        proposal,
        snapshot=_snapshot(),
        is_direct=lambda _: True,
        known_source_ids={"I-aaaa1111"},
    )
    assert [i.text for i in result.proposal.fact_add] == ["TCP/80 is open."]
    assert result.proposal.assumption_add == []


# -- R4: promotion needs new direct evidence ---------------------------------


def test_model_promotion_without_new_evidence_is_rejected() -> None:
    snapshot = _snapshot([AssumptionItem(id="A001", text="maybe HTTP", evidence=["I-aaaa1111"])])
    proposal = SummaryProposal(promote=[ProposedPromotion(id="A001", evidence=["I-aaaa1111"])])
    result = enforce_epistemic_policy(
        proposal, snapshot=snapshot, is_direct=lambda _: True, known_source_ids={"I-aaaa1111"}
    )
    assert result.proposal.promote == []
    assert any("no new direct evidence" in note for note in result.notes)


def test_model_promotion_with_new_direct_evidence_is_allowed() -> None:
    snapshot = _snapshot([AssumptionItem(id="A001", text="maybe HTTP", evidence=["I-aaaa1111"])])
    proposal = SummaryProposal(promote=[ProposedPromotion(id="A001", evidence=["I-bbbb2222"])])
    result = enforce_epistemic_policy(
        proposal,
        snapshot=snapshot,
        is_direct=lambda sid: sid == "I-bbbb2222",
        known_source_ids={"I-aaaa1111", "I-bbbb2222"},
    )
    assert [p.id for p in result.proposal.promote] == ["A001"]


def test_promotion_of_unknown_id_is_dropped() -> None:
    proposal = SummaryProposal(promote=[ProposedPromotion(id="A999", evidence=["I-aaaa1111"])])
    result = enforce_epistemic_policy(
        proposal, snapshot=_snapshot(), is_direct=lambda _: True, known_source_ids={"I-aaaa1111"}
    )
    assert result.proposal.promote == []


# -- R5: structural impossibilities ------------------------------------------


def test_proposal_cannot_express_a_guessing_change_or_fact_deletion() -> None:
    """Enforced by the type, not by a runtime check — the fields simply do not exist."""

    fields = set(SummaryProposal.model_fields)
    assert "guessing" not in fields
    assert "fact_remove" not in fields


# -- end-to-end through the service ------------------------------------------


def test_ingest_creates_fact_and_assumption(service: MemoryService, demo: str) -> None:
    response = service.ingest(
        demo,
        IngestEnvelope.model_validate(
            command_result(["nmap", "-sV", "target"], "80/tcp open  http nginx")
        ),
    )
    memory = service.read_memory(demo)
    facts = [i for _, i in memory_items(memory.fact)]
    assumptions = [i for _, i in memory_items(memory.assumption)]

    assert response.diff.fact_added == ["F001"]
    assert facts[0].text == "TCP/80 is open."
    assert facts[0].evidence == [response.source_id]
    assert assumptions[0].text == "The target may expose an HTTP application."


def test_agent_narrative_only_run_log_cannot_create_a_fact(
    service: MemoryService, demo: str
) -> None:
    """The headline rule: an agent saying something is true does not make it a Fact."""

    response = service.ingest(
        demo,
        IngestEnvelope.model_validate(
            agent_run_log(
                [
                    {
                        "seq": 1,
                        "role": "assistant",
                        "kind": "message",
                        "content": "I ran nmap and port 80 is open, this is probably a web app.",
                    }
                ]
            )
        ),
    )
    assert response.diff.fact_added == []
    assert response.diff.assumption_added  # the claim is retained, but only as a hypothesis
    memory = service.read_memory(demo)
    texts = [i.text for _, i in memory_items(memory.assumption)]
    # The observation the agent narrated is kept — as an Assumption, not a Fact.
    assert "TCP/80 is open." in texts


def test_agent_tool_result_can_create_a_fact(service: MemoryService, demo: str) -> None:
    response = service.ingest(
        demo,
        IngestEnvelope.model_validate(
            agent_run_log(
                [
                    {"seq": 1, "role": "assistant", "kind": "message", "content": "checking"},
                    {
                        "seq": 2,
                        "role": "assistant",
                        "kind": "tool_call",
                        "name": "Bash",
                        "content": "checksec ./chall",
                    },
                    {"seq": 3, "role": "tool", "kind": "tool_result", "content": "NX enabled"},
                ]
            )
        ),
    )
    assert response.diff.fact_added == ["F001"]
    memory = service.read_memory(demo)
    assert [i.text for _, i in memory_items(memory.fact)] == ["NX is enabled on the target binary."]


def test_ids_are_stable_across_updates(service: MemoryService, demo: str) -> None:
    service.ingest(demo, IngestEnvelope.model_validate(human_note("port 80 is open")))
    before = service.read_memory(demo)
    ids_before = sorted(
        i.id for _, i in memory_items(before.fact) + memory_items(before.assumption)
    )

    revision = before.revision
    service.mutate(
        demo,
        MemoryMutation(
            expected_revision=revision,
            actor="ctshen",
            reason="clarify wording",
            operations=[{"op": "replace", "id": "F001", "text": "TCP/80 is open (nginx)."}],
        ),
    )
    after = service.read_memory(demo)
    ids_after = sorted(i.id for _, i in memory_items(after.fact) + memory_items(after.assumption))

    assert ids_before == ids_after
    assert [i.text for _, i in memory_items(after.fact)] == ["TCP/80 is open (nginx)."]


def test_second_ingest_does_not_reuse_ids(service: MemoryService, demo: str) -> None:
    service.ingest(demo, IngestEnvelope.model_validate(human_note("port 80 is open")))
    service.ingest(demo, IngestEnvelope.model_validate(human_note("port 22 is open")))
    memory = service.read_memory(demo)
    ids = [i.id for _, i in memory_items(memory.fact)]
    assert ids == ["F001", "F002"]


def test_challenge_template_is_not_reclassified_by_ingest(service: MemoryService) -> None:
    """Once chosen, a template only changes through an explicit client operation."""

    service.create_challenge(CreateChallengeRequest(id="c", title="C"))
    service.ingest(
        "c",
        IngestEnvelope.model_validate(
            human_note("The HTTP login API sets a cookie; the URL is behind nginx.")
        ),
    )
    assert service.read_memory("c").challenge.template == "generic"


class _UnavailableClassifier(FakeSummarizer):
    """A backend that is down for exactly the advisory call."""

    def classify(self, **kwargs: object) -> ClassificationProposal:
        raise SummarizerUnavailable("backend down")


class _UnauthorizedClassifier(FakeSummarizer):
    """A backend that rejected *our* key — a server misconfiguration, not an outage."""

    def classify(self, **kwargs: object) -> ClassificationProposal:
        raise SummarizerUnauthorized("bad key")


class _BuggyClassifier(FakeSummarizer):
    def classify(self, **kwargs: object) -> ClassificationProposal:
        raise ValueError("bug in classifier")


class _UnknownTemplateClassifier(FakeSummarizer):
    def classify(self, **kwargs: object) -> ClassificationProposal:
        return ClassificationProposal(template="does-not-exist")


class _ProposalSummarizer(FakeSummarizer):
    def __init__(self, proposal: SummaryProposal) -> None:
        self.proposal = proposal

    def summarize(
        self,
        *,
        state: MemorySnapshot,
        source_record: SourceRecord,
        template: TemplateDefinition,
    ) -> SummaryProposal:
        return self.proposal


def _seed_fact(service: MemoryService, challenge_id: str) -> FactItem:
    response = service.ingest(
        challenge_id,
        IngestEnvelope.model_validate(command_result(["nmap"], "8080/tcp open http")),
    )
    assert response.diff.fact_added == ["F001"]
    return [item for _, item in memory_items(service.read_memory(challenge_id).fact)][0]


def test_narrative_only_fact_update_cannot_rewrite_directly_supported_fact(
    runs_dir: Path,
) -> None:
    service = _service_with(FakeSummarizer(), runs_dir)
    service.create_challenge(CreateChallengeRequest(id="c", title="C"))
    original = _seed_fact(service, "c")

    service.summarizer = _ProposalSummarizer(
        SummaryProposal(
            fact_update=[
                ProposedUpdate(
                    id="F001",
                    text="TCP/8080 is closed.",
                    evidence_add=[original.evidence[0]],
                )
            ]
        )
    )
    response = service.ingest(
        "c",
        IngestEnvelope.model_validate(
            agent_run_log(
                [
                    {
                        "seq": 1,
                        "role": "assistant",
                        "kind": "message",
                        "content": "I think port 8080 is probably closed.",
                    }
                ]
            )
        ),
    )

    memory = service.read_memory("c")
    facts = [item for _, item in memory_items(memory.fact)]
    assumptions = [item for _, item in memory_items(memory.assumption)]
    assert facts[0].id == "F001"
    assert facts[0].text == "TCP/8080 is open."
    assert facts[0].evidence == original.evidence
    assert response.diff.fact_updated == []
    assert "TCP/8080 is closed." in [item.text for item in assumptions]


def test_fact_update_with_fresh_direct_evidence_can_correct_fact(runs_dir: Path) -> None:
    service = _service_with(FakeSummarizer(), runs_dir)
    service.create_challenge(CreateChallengeRequest(id="c", title="C"))
    original = _seed_fact(service, "c")

    class CurrentEvidenceUpdate(FakeSummarizer):
        def summarize(
            self,
            *,
            state: MemorySnapshot,
            source_record: SourceRecord,
            template: TemplateDefinition,
        ) -> SummaryProposal:
            return SummaryProposal(
                fact_update=[
                    ProposedUpdate(
                        id="F001",
                        text="TCP/8080 is closed.",
                        evidence_add=[source_record.id],
                    )
                ]
            )

    service.summarizer = CurrentEvidenceUpdate()
    response = service.ingest(
        "c",
        IngestEnvelope.model_validate(command_result(["nmap"], "8080/tcp closed")),
    )

    fact = [item for _, item in memory_items(service.read_memory("c").fact)][0]
    assert response.diff.fact_updated == ["F001"]
    assert fact.text == "TCP/8080 is closed."
    assert fact.evidence[0] == original.evidence[0]
    assert len(fact.evidence) == 2


def test_fact_update_cannot_reuse_unrelated_old_direct_evidence(runs_dir: Path) -> None:
    service = _service_with(FakeSummarizer(), runs_dir)
    service.create_challenge(CreateChallengeRequest(id="c", title="C"))
    _seed_fact(service, "c")
    old_other = service.ingest(
        "c", IngestEnvelope.model_validate(command_result(["nmap"], "22/tcp open ssh"))
    ).source_id

    service.summarizer = _ProposalSummarizer(
        SummaryProposal(
            fact_update=[
                ProposedUpdate(id="F001", text="TCP/8080 is closed.", evidence_add=[old_other])
            ]
        )
    )
    service.ingest(
        "c",
        IngestEnvelope.model_validate(
            agent_run_log(
                [
                    {
                        "seq": 1,
                        "role": "assistant",
                        "kind": "message",
                        "content": "I think 8080 is closed.",
                    }
                ]
            )
        ),
    )

    facts = [item for _, item in memory_items(service.read_memory("c").fact)]
    assert facts[0].text == "TCP/8080 is open."


@pytest.mark.parametrize("section", ["attacker_section", "../../weird", ""])
@pytest.mark.parametrize(
    ("field", "item_id", "container_name"),
    [("fact_update", "F001", "fact"), ("assumption_update", "A001", "assumption")],
)
def test_proposed_update_sections_are_template_constrained(
    runs_dir: Path, section: str, field: str, item_id: str, container_name: str
) -> None:
    service = _service_with(FakeSummarizer(), runs_dir)
    service.create_challenge(CreateChallengeRequest(id="c", title="C"))
    _seed_fact(service, "c")

    proposal = SummaryProposal.model_validate({field: [{"id": item_id, "section": section}]})
    service.summarizer = _ProposalSummarizer(proposal)
    service.ingest("c", IngestEnvelope.model_validate(human_note("move it")))

    memory = service.read_memory("c")
    container = getattr(memory, container_name)
    assert "attacker_section" not in container
    assert "../../weird" not in container
    assert "" not in container
    assert any(item.id == item_id for item in container["general"])


def _service_with(summarizer: object, runs_dir: Path) -> MemoryService:
    return MemoryService.build(
        runs_dir=runs_dir,
        templates_dir=TEMPLATES_DIR,
        summarizer=summarizer,  # type: ignore[arg-type]
    )


def test_an_unavailable_classifier_falls_back_instead_of_blocking_creation(
    runs_dir: Path,
) -> None:
    """Classification is advisory: a down backend must not stop a human opening a board.

    The template is a layout, not a truth claim, and ``POST /template`` can fix it later.
    Refusing to create the challenge would strand the human with nowhere to put notes.
    """

    service = _service_with(_UnavailableClassifier(), runs_dir)
    response = service.create_challenge(
        CreateChallengeRequest(id="c", title="C", initial_input="an HTTP login form")
    )
    assert response.template == "generic"
    assert service.read_memory("c").revision == 0


def test_a_rejected_key_still_surfaces_from_creation(runs_dir: Path) -> None:
    """The one thing the fallback must not swallow.

    A bad key would otherwise create every challenge as ``generic`` forever, looking like
    a classifier that simply never matches anything.
    """

    service = _service_with(_UnauthorizedClassifier(), runs_dir)
    with pytest.raises(SummarizerUnauthorized):
        service.create_challenge(
            CreateChallengeRequest(id="c", title="C", initial_input="an HTTP login form")
        )


def test_programming_error_in_classifier_is_not_silently_generic(runs_dir: Path) -> None:
    service = _service_with(_BuggyClassifier(), runs_dir)
    with pytest.raises(ValueError, match="bug in classifier"):
        service.create_challenge(
            CreateChallengeRequest(id="c", title="C", initial_input="an HTTP login form")
        )


def test_unknown_template_from_classifier_is_not_silently_generic(runs_dir: Path) -> None:
    service = _service_with(_UnknownTemplateClassifier(), runs_dir)
    with pytest.raises(TemplateNotFound):
        service.create_challenge(
            CreateChallengeRequest(id="c", title="C", initial_input="an HTTP login form")
        )


def memory_items(container: dict[str, list]) -> list[tuple[str, object]]:  # type: ignore[type-arg]
    return [(section, item) for section, items in container.items() for item in items]
