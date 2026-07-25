"""The Summarizer: the *only* model-driven component inside Memory Core.

Responsibilities (設計規劃書 §6.1)

1. help pick a template when a challenge is first created;
2. turn ``(new source record + current memory + template layout)`` into a
   :class:`SummaryProposal`;
3. classify content as new Fact / new Assumption / update / nothing worth keeping;
4. maintain the challenge summary;
5. answer natural-language queries strictly from current whiteboard state.

Explicit non-responsibilities: choosing exploits, running shell commands, driving a YOLO
loop, orchestrating an agent, or editing the Guessing catalog.

================================  EPISTEMIC POLICY  ============================

These rules are stated here, enforced in :func:`enforce_epistemic_policy`, restated in
the system prompt sent to the backend, and covered by ``tests/test_summarizer_policy.py``.
They exist because LongMemEval (arXiv:2410.10813) treats *abstention* as a first-class
capability: a memory system that promotes plausible inference to certainty in order to
look useful is worse than one that says "not established yet".

R1. A Fact must cite at least one journal source ID that exists.
R2. A Fact must be supported by **direct evidence**:
      * ``command_result``                                  -> direct
      * ``run_log`` containing at least one ``tool_result``  -> direct
      * a **human** ``note``                                 -> direct
      * an **agent** ``note``/narrative-only ``run_log``      -> NOT direct
    The human-note case deserves comment.  A human note may contain either an
    *observation* ("I ran nmap and port 80 is open") or an *inference* ("this is probably
    SQLi").  Only the observation may support a Fact; the inference must stay an
    Assumption.  That distinction is a judgment call and therefore lives in the prompt,
    not in the gate.  What the gate *can* enforce mechanically is the vendor-neutral
    asymmetry the whole design rests on: a first-party human report is admissible
    evidence, an external agent's narrative never is.  An agent must show a tool result.
R3. Insufficient evidence => Assumption, or nothing at all.  A Fact proposal that fails
    R1/R2 is **downgraded to an Assumption**, not silently accepted and not fatal.
R4. Promotion Assumption -> Fact requires evidence the item did not already carry, and
    that evidence must itself be direct.  A human override that lacks new evidence is a
    legitimate operation, but it goes through ``POST /mutate`` with an actor and a
    reason — never through the model pretending it verified something.
R5. The Summarizer cannot touch the Guessing catalog, and cannot delete a Fact.  Both are
    enforced structurally by :class:`SummaryProposal` having no such fields.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from typing import Protocol

from pydantic import ValidationError

from .errors import ProposalInvalid
from .models import (
    GENERAL_SECTION,
    AnswerProposal,
    ClassificationProposal,
    MemorySnapshot,
    ProposedDemotion,
    ProposedItem,
    ProposedPromotion,
    ProposedUpdate,
    SourceRecord,
    SummaryProposal,
    TemplateDefinition,
)

#: Callable that answers "is this journal source ID direct evidence?".
EvidenceResolver = Callable[[str], bool]


class Summarizer(Protocol):
    """The Memory Core Summarizer interface.

    Deviation from the original single-method sketch: classification and query answering
    are also model-driven per 設計規劃書 §6.1 items 1 and 5, so they are part of the
    interface rather than hidden behind ``summarize``.  Everything else in Memory Core is
    deterministic.
    """

    def summarize(
        self,
        *,
        state: MemorySnapshot,
        source_record: SourceRecord,
        template: TemplateDefinition,
    ) -> SummaryProposal: ...

    def classify(
        self,
        *,
        initial_input: str,
        templates: Sequence[TemplateDefinition],
    ) -> ClassificationProposal: ...

    def answer(
        self,
        *,
        state: MemorySnapshot,
        question: str,
        layers: Sequence[str],
    ) -> AnswerProposal: ...


# ---------------------------------------------------------------------------
# Evidence classification
# ---------------------------------------------------------------------------


def is_direct_evidence(record: SourceRecord) -> bool:
    """Apply rule R2 to a single journal record."""

    if record.type != "input":
        # Mutations are human overrides; they never travel through the Summarizer.
        return False
    if record.input_type == "command_result":
        return True
    if record.input_type == "note":
        return record.source is not None and record.source.type == "human"
    if record.input_type == "run_log":
        events = (record.content or {}).get("events", [])
        if not isinstance(events, list):
            return False
        return any(
            isinstance(event, dict) and event.get("kind") == "tool_result" for event in events
        )
    return False


def evidence_resolver(records: Sequence[SourceRecord]) -> EvidenceResolver:
    known = {r.id: is_direct_evidence(r) for r in records}

    def resolve(source_id: str) -> bool:
        return known.get(source_id, False)

    return resolve


# ---------------------------------------------------------------------------
# Policy gate
# ---------------------------------------------------------------------------


class PolicyResult:
    """A proposal after the gate, plus a human-readable record of what was changed."""

    def __init__(self, proposal: SummaryProposal, notes: list[str]) -> None:
        self.proposal = proposal
        self.notes = notes


def enforce_epistemic_policy(
    proposal: SummaryProposal,
    *,
    snapshot: MemorySnapshot,
    is_direct: EvidenceResolver,
    known_source_ids: set[str],
    current_source_id: str | None = None,
) -> PolicyResult:
    """Apply R1-R5 to a raw proposal.

    Never raises for a merely over-confident proposal: over-claims are *downgraded* so
    the useful part of the summary survives.  Structurally impossible claims (unknown
    IDs) are dropped.  The result is safe to commit.
    """

    notes: list[str] = []
    fact_ids = {item.id for _, item in snapshot.iter_facts()}
    assumption_ids = {item.id for _, item in snapshot.iter_assumptions()}

    kept_facts: list[ProposedItem] = []
    downgraded: list[ProposedItem] = []

    for item in proposal.fact_add:
        citations = [e for e in item.evidence if e in known_source_ids]  # R1
        if len(citations) != len(item.evidence):
            notes.append(f"dropped unknown evidence ids on proposed fact {item.text[:40]!r}")
        if citations and any(is_direct(e) for e in citations):  # R2
            kept_facts.append(item.model_copy(update={"evidence": citations}))
        else:  # R3
            notes.append(
                f"downgraded proposed fact to assumption (no direct evidence): {item.text[:60]!r}"
            )
            downgraded.append(item.model_copy(update={"evidence": citations}))

    kept_promotions: list[ProposedPromotion] = []
    for promotion in proposal.promote:
        if promotion.id not in assumption_ids:
            notes.append(f"dropped promotion of unknown assumption {promotion.id}")
            continue
        found = snapshot.find(promotion.id)
        already = set(found[2].evidence) if found else set()
        fresh = [e for e in promotion.evidence if e in known_source_ids and e not in already]
        if not fresh or not any(is_direct(e) for e in fresh):  # R4
            notes.append(
                f"rejected promotion of {promotion.id}: no new direct evidence "
                "(a human override must use POST /mutate with a reason)"
            )
            continue
        kept_promotions.append(promotion.model_copy(update={"evidence": fresh}))

    def _clean_fact_updates(updates: Sequence[ProposedUpdate]) -> list[ProposedUpdate]:
        cleaned: list[ProposedUpdate] = []
        for update in updates:
            found = snapshot.find(update.id)
            if found is None or found[0] != "fact":
                notes.append(f"dropped fact update for unknown id {update.id}")
                continue
            _, _, item = found
            citations = [e for e in update.evidence_add if e in known_source_ids]
            changes_text = update.text is not None and update.text != item.text
            if changes_text:
                updated_text = update.text
                assert updated_text is not None
                fresh_current = (
                    current_source_id is not None
                    and current_source_id in citations
                    and current_source_id not in item.evidence
                    and is_direct(current_source_id)
                )
                if not fresh_current:
                    notes.append(
                        f"downgraded unsafe fact update to assumption: {updated_text[:60]!r}"
                    )
                    downgraded.append(
                        ProposedItem(
                            section=update.section or GENERAL_SECTION,
                            text=updated_text,
                            evidence=citations,
                        )
                    )
                    continue
            cleaned.append(
                update.model_copy(
                    update={
                        "evidence_add": citations,
                    }
                )
            )
        return cleaned

    def _clean_assumption_updates(updates: Sequence[ProposedUpdate]) -> list[ProposedUpdate]:
        cleaned: list[ProposedUpdate] = []
        for update in updates:
            if update.id not in assumption_ids:
                notes.append(f"dropped assumption update for unknown id {update.id}")
                continue
            cleaned.append(
                update.model_copy(
                    update={
                        "evidence_add": [e for e in update.evidence_add if e in known_source_ids]
                    }
                )
            )
        return cleaned

    fact_updates = _clean_fact_updates(proposal.fact_update)
    assumption_updates = _clean_assumption_updates(proposal.assumption_update)

    assumption_removals: list[str] = []
    for item_id in proposal.assumption_remove:
        if item_id in assumption_ids:
            assumption_removals.append(item_id)
        else:
            notes.append(f"dropped removal of unknown assumption {item_id}")

    demotions: list[ProposedDemotion] = []
    for demotion in proposal.demote:
        if demotion.id in fact_ids:
            demotions.append(demotion)
        else:
            notes.append(f"dropped demotion of unknown fact {demotion.id}")

    assumption_adds = [
        item.model_copy(update={"evidence": [e for e in item.evidence if e in known_source_ids]})
        for item in list(proposal.assumption_add) + downgraded
    ]

    cleaned = proposal.model_copy(
        update={
            "fact_add": kept_facts,
            "fact_update": fact_updates,
            "assumption_add": assumption_adds,
            "assumption_update": assumption_updates,
            "assumption_remove": assumption_removals,
            "promote": kept_promotions,
            "demote": demotions,
        }
    )
    return PolicyResult(cleaned, notes)


# ---------------------------------------------------------------------------
# Proposal parsing (shared by every LLM-backed provider)
# ---------------------------------------------------------------------------

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> str:
    """Pull the JSON object out of a chat completion.

    Deliberately tolerant of fenced code blocks and leading prose, because we do not
    assume any provider-specific structured-output extension exists.
    """

    match = _JSON_BLOCK.search(text)
    if match:
        return match.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    return text.strip()


def parse_proposal(text: str) -> SummaryProposal:
    """Validate a raw model response, raising :class:`ProposalInvalid` on failure."""

    payload = extract_json(text)
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ProposalInvalid(f"summarizer did not return JSON: {exc}", raw=text[:500]) from exc
    try:
        return SummaryProposal.model_validate(data)
    except ValidationError as exc:
        raise ProposalInvalid(
            "summarizer JSON did not match SummaryProposal",
            errors=exc.errors(include_url=False),
        ) from exc


# ---------------------------------------------------------------------------
# Fake summarizer — the default for tests and for the mock server
# ---------------------------------------------------------------------------

_PORT_PATTERNS = [
    re.compile(r"\b(\d{1,5})/tcp\s+open\b", re.IGNORECASE),
    re.compile(r"\bport\s+(\d{1,5})\s+is\s+open\b", re.IGNORECASE),
]
_NX_PATTERN = re.compile(r"\bNX\s+(enabled|disabled)\b", re.IGNORECASE)


class FakeSummarizer:
    """Deterministic, rule-driven stand-in for a real model.

    Two jobs:

    * make the default test suite fully offline and reproducible;
    * be the ``--fake-summarizer`` mock server that Person 2 and Person 3 develop
      against without needing an AIS3 key (共通設計起點 §7).

    It follows the same epistemic policy as a real backend — it proposes Facts only for
    observations and Assumptions for inferences — so a client cannot accidentally build
    against looser behaviour than production.
    """

    def summarize(
        self,
        *,
        state: MemorySnapshot,
        source_record: SourceRecord,
        template: TemplateDefinition,
    ) -> SummaryProposal:
        text = _record_text(source_record)
        evidence = [source_record.id]
        fact_section = _pick(template.fact_sections, ["target", "general"])
        assumption_section = _pick(template.assumption_sections, ["next_checks", "general"])

        facts: list[ProposedItem] = []
        assumptions: list[ProposedItem] = []
        seen_ports: set[str] = set()

        for pattern in _PORT_PATTERNS:
            for port in pattern.findall(text):
                if port in seen_ports:
                    continue
                seen_ports.add(port)
                facts.append(
                    ProposedItem(
                        section=fact_section,
                        text=f"TCP/{port} is open.",
                        evidence=list(evidence),
                    )
                )
                if port in {"80", "443", "8080", "8000"}:
                    assumptions.append(
                        ProposedItem(
                            section=assumption_section,
                            text="The target may expose an HTTP application.",
                            evidence=list(evidence),
                        )
                    )

        nx = _NX_PATTERN.search(text)
        if nx:
            facts.append(
                ProposedItem(
                    section=_pick(template.fact_sections, ["binary", "artifacts", "general"]),
                    text=f"NX is {nx.group(1).lower()} on the target binary.",
                    evidence=list(evidence),
                )
            )

        if not facts and not assumptions and text.strip():
            assumptions.append(
                ProposedItem(
                    section=assumption_section,
                    text=_first_sentence(text),
                    evidence=list(evidence),
                )
            )

        summary = state.challenge.summary
        if facts or assumptions:
            total = len(state.all_ids()) + len(facts) + len(assumptions)
            summary = f"{total} memory items recorded."

        return SummaryProposal(
            challenge_summary=summary,
            fact_add=facts,
            assumption_add=assumptions,
            note=f"fake summarizer processed {source_record.input_type or source_record.type}",
        )

    def classify(
        self,
        *,
        initial_input: str,
        templates: Sequence[TemplateDefinition],
    ) -> ClassificationProposal:
        haystack = initial_input.lower()
        best: tuple[int, str] = (0, "generic")
        for template in templates:
            hits = sum(1 for hint in template.classifier_hints if hint.lower() in haystack)
            if hits > best[0]:
                best = (hits, template.id)
        return ClassificationProposal(
            template=best[1],
            confidence="high" if best[0] >= 2 else "low",
            rationale=f"matched {best[0]} classifier hint(s)",
        )

    def answer(
        self,
        *,
        state: MemorySnapshot,
        question: str,
        layers: Sequence[str],
    ) -> AnswerProposal:
        words = {w for w in re.findall(r"[a-z0-9/]+", question.lower()) if len(w) > 2}
        hits: list[tuple[str, str]] = []
        if "fact" in layers:
            hits += [(i.id, i.text) for _, i in state.iter_facts()]
        if "assumption" in layers:
            hits += [(i.id, i.text) for _, i in state.iter_assumptions()]

        matched = [(i, t) for i, t in hits if words & set(re.findall(r"[a-z0-9/]+", t.lower()))]
        chosen = matched or hits
        if not chosen:
            return AnswerProposal(answer="Nothing has been recorded for this challenge yet.")
        body = " ".join(f"[{i}] {t}" for i, t in chosen)
        return AnswerProposal(answer=body, references=[i for i, _ in chosen])


def _record_text(record: SourceRecord) -> str:
    """Flatten a journal record into the text the Summarizer reasons over."""

    content = record.content or {}
    if record.input_type == "note":
        return str(content.get("content", ""))
    if record.input_type == "command_result":
        return "\n".join(
            [
                " ".join(str(c) for c in _as_list(content.get("command"))),
                str(content.get("stdout", "")),
                str(content.get("stderr", "")),
            ]
        )
    if record.input_type == "run_log":
        events = content.get("events", [])
        if isinstance(events, list):
            return "\n".join(
                str(event.get("content", "")) for event in events if isinstance(event, dict)
            )
    return json.dumps(content, ensure_ascii=False)


def _as_list(value: object) -> list[object]:
    """Journal content is an untyped dict; narrow before iterating it."""

    return value if isinstance(value, list) else []


def _pick(available: Sequence[str], preferences: Sequence[str]) -> str:
    for preference in preferences:
        if preference in available:
            return preference
    return available[0] if available else GENERAL_SECTION


def _first_sentence(text: str) -> str:
    stripped = " ".join(text.split())
    for sep in (". ", "! ", "? "):
        index = stripped.find(sep)
        if index != -1:
            return stripped[: index + 1]
    return stripped[:200]
