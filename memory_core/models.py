"""Canonical domain model for Memory Core v2.

This module is the **single source of truth** for the shared contract described in
``docs/CTF共享記憶系統_v2_共通設計起點.md`` §1.  Everything under ``spec/`` is generated
from these types by ``scripts/gen_spec.py``; nothing else may hand-maintain a
competing schema.

Three user-facing memory concepts exist and no more (設計規劃書 §4):

* **Fact**       — atomic, challenge-specific, confirmed by *direct evidence* or an
                   explicit human override.
* **Assumption** — atomic, challenge-specific, plausible and testable, not yet
                   sufficiently verified.
* **Guessing**   — a curated technique catalog owned by the *template*, never
                   free-form model brainstorming.

The permitted epistemic evolution is::

    Guessing technique -> concrete hypothesis -> Assumption -> direct evidence -> Fact

and never::

    Guessing -> "the model thinks it sounds right" -> Fact

Note that :class:`SummaryProposal` deliberately has **no guessing field**: the
Summarizer is structurally incapable of touching the technique catalog.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 2

#: The readable layers of the whiteboard.  ``guessing`` is readable but, unlike the other
#: two, it is owned by the template rather than by the challenge.
MemoryLayer = Literal["fact", "assumption", "guessing"]

#: Vendor-neutral execution provenance for agent tool results.  A tool_result without
#: this field is accepted for wire compatibility but is deliberately treated as
#: ``unknown`` by the evidence policy rather than as verified direct evidence.
ExecutionOutcome = Literal["succeeded", "failed", "denied", "interrupted", "unknown"]

#: A query searches the epistemic layers by default; Guessing is opt-in because it says
#: what could be tried, not what is known.
_DEFAULT_QUERY_LAYERS: list[MemoryLayer] = ["fact", "assumption"]

# The section every template must provide so that reclassification can always find a
# home for an item it cannot map (共通設計起點 §6).
GENERAL_SECTION = "general"

#: A challenge id becomes a directory name under ``runs/``, so it must be a *name* and
#: never a path.  The leading character is restricted specifically to rule out ``.`` and
#: ``..``, which are valid-looking ids that would otherwise resolve outside the runs
#: directory and create a challenge that ``GET /v1/challenges`` cannot even list.
#: ``memory_core.store`` re-checks this on every path it builds, so the guarantee does not
#: depend on request validation alone.
CHALLENGE_ID_PATTERN = r"^[A-Za-z0-9_-][A-Za-z0-9._-]*$"

_CHALLENGE_ID_RE = re.compile(CHALLENGE_ID_PATTERN)


def is_valid_challenge_id(challenge_id: str) -> bool:
    """Whether ``challenge_id`` is safe to use as a directory name.

    Kept as a predicate rather than a raising validator so this module stays a pure
    contract definition; the persistence layer decides which error that maps to.
    """

    return bool(_CHALLENGE_ID_RE.match(challenge_id))


class _Model(BaseModel):
    """Base for every contract type: unknown fields are rejected, not silently dropped."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# ---------------------------------------------------------------------------
# Source / evidence contract (共通設計起點 §3)
# ---------------------------------------------------------------------------


class InputSource(_Model):
    """Who produced an input.

    Vendor-neutral on purpose.  Claude SDK objects are normalized into this shape by
    the Agent Bridge (Person 3) *before* they reach Memory Core; provider-specific
    classes never become part of the domain model.
    """

    type: Literal["human", "agent"]
    actor: str | None = Field(default=None, description="Human username, when type=human.")
    provider: str | None = Field(default=None, description='e.g. "anthropic", when type=agent.')
    model: str | None = None
    run_id: str | None = None
    session_id: str | None = None


class AgentRunEvent(_Model):
    """One normalized event from an external agent run.

    ``kind`` and ``outcome`` together carry the epistemic weight.  A ``tool_result`` is
    direct evidence only when its outcome says the tool actually executed
    (``succeeded`` or ``failed``).  Missing, denied, interrupted or unknown outcomes are
    not Fact-grade evidence.
    """

    seq: int = Field(ge=1)
    role: Literal["assistant", "tool", "user", "system"]
    kind: Literal["message", "tool_call", "tool_result"]
    name: str | None = Field(default=None, description="Tool name, for tool_call/tool_result.")
    content: str
    outcome: ExecutionOutcome | None = Field(
        default=None,
        description=(
            "Execution provenance for tool_result. succeeded/failed mean the tool ran; "
            "denied/interrupted/unknown do not grant direct-evidence status. Omitted "
            "legacy tool_result events are treated as unknown."
        ),
    )


class NoteInput(_Model):
    """Free-form human note.  Ordinary human hypotheses default to Assumption."""

    type: Literal["note"] = "note"
    content: str


class CommandResultInput(_Model):
    """Result of a command the human ran.  Direct evidence: may support a Fact."""

    type: Literal["command_result"] = "command_result"
    command: list[str]
    exit_code: int
    stdout: str = ""
    stderr: str = ""


class RunLogInput(_Model):
    """Normalized external-agent run log."""

    type: Literal["run_log"] = "run_log"
    events: list[AgentRunEvent]


InputPayload = Annotated[
    NoteInput | CommandResultInput | RunLogInput,
    Field(discriminator="type"),
]


class IngestEnvelope(_Model):
    """The one and only ingest contract, shared by humans and agents."""

    source: InputSource
    input: InputPayload


# ---------------------------------------------------------------------------
# Journal (設計規劃書 §5.2)
# ---------------------------------------------------------------------------


class SourceRecord(_Model):
    """An append-only journal entry.

    The journal is a *source/evidence record*, not a fourth semantic memory layer.
    State is never rebuilt from it; it exists so that summaries never replace the raw
    experience they were derived from (Generative Agents, arXiv:2304.03442) and so that
    Fact/Assumption entries can cite a stable evidence ID.
    """

    id: str = Field(description='Stable source ID: "I-<hex>" for inputs, "M-<hex>" for mutations.')
    type: Literal["input", "mutation"]
    at: str = Field(description="ISO-8601 UTC timestamp.")
    actor: str = Field(description='"human" / "agent" or a named actor.')
    source: InputSource | None = None
    input_type: Literal["note", "command_result", "run_log"] | None = None
    content: dict[str, object] | None = Field(
        default=None, description="Verbatim input payload, for type=input."
    )
    reason: str | None = Field(default=None, description="Human override rationale, for mutations.")
    operations: list[dict[str, object]] | None = None


# ---------------------------------------------------------------------------
# Memory items (設計規劃書 §5.1)
# ---------------------------------------------------------------------------


class MemoryItem(_Model):
    """Shared shape of Fact and Assumption entries.

    Following A-MEM (arXiv:2502.12110) we keep atomic notes with stable IDs and light
    metadata, and we deliberately reject its vector store, embeddings and autonomous
    graph linking.  ``id`` never changes for the lifetime of an item, including across
    template reclassification.
    """

    id: str = Field(
        pattern=r"^[FA]\d{3,}$",
        description=(
            "Stable identity within a layer. The F/A prefix always matches the layer the "
            "item currently lives in, so crossing layers mints a new id: promoting A017 "
            "produces a new F-item carrying promoted_from='A017'. An id is never reused, "
            "even after the item it named is removed or promoted away."
        ),
    )
    text: str
    evidence: list[str] = Field(
        default_factory=list, description="Journal source IDs supporting this item."
    )
    source_actor: str | None = None
    tags: list[str] = Field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None
    promoted_from: str | None = Field(
        default=None,
        description=(
            "The id this item had in the other layer before a promotion or demotion. "
            "Follow the chain to reconstruct an item's epistemic history."
        ),
    )


class FactItem(MemoryItem):
    """Confirmed by direct evidence or an explicit human override."""


class AssumptionItem(MemoryItem):
    """Plausible and testable, but not yet sufficiently verified."""


class GuessingTechniqueState(_Model):
    tried: bool = False
    note: str | None = None


class GuessingState(_Model):
    """Per-challenge local state over a template-owned catalog.

    ``state.yaml`` stores only the catalog identity plus local ``tried`` flags; the full
    technique metadata is always resolved from the template registry (設計規劃書 §4.3).
    """

    catalog: str = Field(pattern=r"^[a-z0-9_-]+@\d+$", description='e.g. "web@1".')
    state: dict[str, GuessingTechniqueState] = Field(default_factory=dict)


class ChallengeMeta(_Model):
    id: str
    title: str
    category: str = "generic"
    template: str = "generic"
    template_version: int = 1
    summary: str | None = None


class SnapshotMeta(_Model):
    last_input_id: str | None = None
    last_updated_at: str | None = None
    fact_seq: int = Field(
        default=0,
        ge=0,
        description=(
            "High-water mark for allocated F ids. Persisted so that an id is never reused "
            "after its item is removed or promoted into the other layer."
        ),
    )
    assumption_seq: int = Field(default=0, ge=0, description="High-water mark for A ids.")


class MemorySnapshot(_Model):
    """The whole persisted whiteboard for one challenge — i.e. ``state.yaml``."""

    schema_version: Literal[2] = 2
    revision: int = Field(default=0, ge=0)
    challenge: ChallengeMeta
    fact: dict[str, list[FactItem]] = Field(default_factory=dict)
    assumption: dict[str, list[AssumptionItem]] = Field(default_factory=dict)
    guessing: GuessingState
    meta: SnapshotMeta = Field(default_factory=SnapshotMeta)

    def iter_facts(self) -> list[tuple[str, FactItem]]:
        return [(section, item) for section, items in self.fact.items() for item in items]

    def iter_assumptions(self) -> list[tuple[str, AssumptionItem]]:
        return [(section, item) for section, items in self.assumption.items() for item in items]

    def all_ids(self) -> set[str]:
        return {i.id for _, i in self.iter_facts()} | {i.id for _, i in self.iter_assumptions()}

    def find(self, item_id: str) -> tuple[Literal["fact", "assumption"], str, MemoryItem] | None:
        for section, fact in self.iter_facts():
            if fact.id == item_id:
                return ("fact", section, fact)
        for section, assumption in self.iter_assumptions():
            if assumption.id == item_id:
                return ("assumption", section, assumption)
        return None


# ---------------------------------------------------------------------------
# Templates (設計規劃書 §7)
# ---------------------------------------------------------------------------


class GuessingTechnique(_Model):
    """One curated technique.  IDs are stable within a template version."""

    id: str = Field(pattern=r"^[A-Z0-9]+-[A-Z0-9-]+$")
    title: str
    tags: list[str] = Field(default_factory=list)
    description: str | None = None


class TemplateDefinition(_Model):
    """A CTF category template.

    Memory Core owns the loader, the schema and the reclassification algorithm.  The
    per-category technique content is supplied by the template component.
    """

    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    version: int = Field(ge=1)
    description: str = ""
    classifier_hints: list[str] = Field(default_factory=list)
    fact_sections: list[str]
    assumption_sections: list[str]
    guessing: list[GuessingTechnique] = Field(default_factory=list)

    @property
    def catalog(self) -> str:
        return f"{self.id}@{self.version}"


class TemplateSummary(_Model):
    """Public template metadata exposed to clients.

    Clients need template ids for explicit reclassification and Guessing labels for
    rendering.  Memory Core still owns loading, validation, section remapping and the
    authoritative per-challenge guessing state.
    """

    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    version: int = Field(ge=1)
    catalog: str = Field(pattern=r"^[a-z0-9_-]+@\d+$")
    description: str = ""
    fact_sections: list[str]
    assumption_sections: list[str]
    guessing: list[GuessingTechnique] = Field(default_factory=list)


class TemplateListResponse(_Model):
    templates: list[TemplateSummary]


# ---------------------------------------------------------------------------
# Mutation (設計規劃書 §8.6)
# ---------------------------------------------------------------------------


class AddOperation(_Model):
    op: Literal["add"] = "add"
    layer: Literal["fact", "assumption"]
    section: str = GENERAL_SECTION
    text: str
    evidence: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class ReplaceOperation(_Model):
    op: Literal["replace"] = "replace"
    id: str
    text: str
    evidence: list[str] | None = None
    tags: list[str] | None = None


class RemoveOperation(_Model):
    op: Literal["remove"] = "remove"
    id: str


class PromoteOperation(_Model):
    """Assumption -> Fact.  This is the *only* sanctioned human override path."""

    op: Literal["promote"] = "promote"
    id: str
    to: Literal["fact"] = "fact"
    section: str | None = None


class DemoteOperation(_Model):
    """Fact -> Assumption, e.g. when new evidence undermines a previous conclusion."""

    op: Literal["demote"] = "demote"
    id: str
    to: Literal["assumption"] = "assumption"
    section: str | None = None


class MoveOperation(_Model):
    """Change an item's section only.  Never changes its epistemic level."""

    op: Literal["move"] = "move"
    id: str
    section: str


class MarkTriedOperation(_Model):
    """Record that a Guessing technique has been attempted for this challenge.

    Beyond the six operations in the original brief, and deliberately so: ``state.yaml``
    stores per-challenge ``tried`` flags (設計規劃書 §4.3) and ``GuessRequest.only_untried``
    is meaningless without a sanctioned way to set them.  It is still a *controlled*
    operation over template-owned identifiers — it can flip a flag and attach a note, and
    it can neither add a technique to the catalog nor change its metadata.
    """

    op: Literal["mark_tried"] = "mark_tried"
    technique: str
    tried: bool = True
    note: str | None = None


MutationOperation = Annotated[
    AddOperation
    | ReplaceOperation
    | RemoveOperation
    | PromoteOperation
    | DemoteOperation
    | MoveOperation
    | MarkTriedOperation,
    Field(discriminator="op"),
]


class MemoryMutation(_Model):
    """Controlled human mutation.  There is deliberately no arbitrary YAML patch API."""

    expected_revision: int = Field(ge=0)
    actor: str
    reason: str = Field(min_length=1, description="Required: overrides must be attributable.")
    operations: list[MutationOperation] = Field(min_length=1)


class TemplateChangeRequest(_Model):
    template: str
    expected_revision: int = Field(ge=0)
    actor: str
    reason: str = Field(min_length=1)


# ---------------------------------------------------------------------------
# Semantic diff (設計規劃書 §9)
# ---------------------------------------------------------------------------


class TemplateChange(_Model):
    from_: str = Field(alias="from")
    to: str


class MemoryDiff(_Model):
    fact_added: list[str] = Field(default_factory=list)
    fact_updated: list[str] = Field(default_factory=list)
    fact_removed: list[str] = Field(default_factory=list)
    assumption_added: list[str] = Field(default_factory=list)
    assumption_updated: list[str] = Field(default_factory=list)
    assumption_removed: list[str] = Field(default_factory=list)
    promoted: list[str] = Field(default_factory=list)
    demoted: list[str] = Field(default_factory=list)
    template_changed: TemplateChange | None = None

    def is_empty(self) -> bool:
        return not any(
            [
                self.fact_added,
                self.fact_updated,
                self.fact_removed,
                self.assumption_added,
                self.assumption_updated,
                self.assumption_removed,
                self.promoted,
                self.demoted,
                self.template_changed,
            ]
        )


class DiffEnvelope(_Model):
    """Returned by every successful state-changing operation.

    This is a machine-readable collaboration artifact.  Rendering belongs to the Human
    Client (Person 2); Memory Core never formats it for a terminal.
    """

    revision_before: int
    revision_after: int
    summary: str | None = None
    diff: MemoryDiff


# ---------------------------------------------------------------------------
# Summarizer proposals (設計規劃書 §6)
# ---------------------------------------------------------------------------


class ProposedItem(_Model):
    """A new Fact/Assumption proposed by the Summarizer.

    No ``id`` field: IDs are allocated by Memory Core at commit time so the model can
    never invent, collide with, or overwrite an existing identity.
    """

    section: str = GENERAL_SECTION
    text: str = Field(min_length=1)
    evidence: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class ProposedUpdate(_Model):
    """An in-place revision of an existing item (LongMemEval knowledge update)."""

    id: str
    text: str | None = None
    section: str | None = None
    evidence_add: list[str] = Field(default_factory=list)


class ProposedPromotion(_Model):
    """Assumption -> Fact.  Requires evidence that the item did not already carry."""

    id: str
    evidence: list[str] = Field(min_length=1)
    section: str | None = None


class ProposedDemotion(_Model):
    id: str
    reason: str
    section: str | None = None


class SummaryProposal(_Model):
    """What the Summarizer may propose — and, by omission, what it may not.

    There is no ``guessing`` field.  The technique catalog belongs to the template and
    is unreachable from here by construction, not merely by convention.

    There is also no ``fact_remove`` field.  A model may *demote* a Fact it now believes
    is unsupported — which is visible in the diff and keeps the item — but it may never
    delete one.  Erasing a Fact requires a human mutation with a stated reason.
    """

    challenge_summary: str | None = None
    fact_add: list[ProposedItem] = Field(default_factory=list)
    fact_update: list[ProposedUpdate] = Field(default_factory=list)
    assumption_add: list[ProposedItem] = Field(default_factory=list)
    assumption_update: list[ProposedUpdate] = Field(default_factory=list)
    assumption_remove: list[str] = Field(default_factory=list)
    promote: list[ProposedPromotion] = Field(default_factory=list)
    demote: list[ProposedDemotion] = Field(default_factory=list)
    note: str | None = Field(default=None, description="Short rationale, surfaced as diff summary.")

    def is_noop(self) -> bool:
        return not any(
            [
                self.challenge_summary,
                self.fact_add,
                self.fact_update,
                self.assumption_add,
                self.assumption_update,
                self.assumption_remove,
                self.promote,
                self.demote,
            ]
        )


class AnswerProposal(_Model):
    """A Summarizer's answer to a natural-language query.

    The service filters ``references`` against the live snapshot before returning it, so
    an invented ID can never reach a client.
    """

    answer: str
    references: list[str] = Field(default_factory=list)


class ClassificationProposal(_Model):
    """Initial template selection.  Only ever applied once, at challenge creation."""

    template: str
    confidence: Literal["low", "medium", "high"] = "low"
    rationale: str | None = None


# ---------------------------------------------------------------------------
# Requests / responses (設計規劃書 §8)
# ---------------------------------------------------------------------------


class CreateChallengeRequest(_Model):
    id: str = Field(pattern=CHALLENGE_ID_PATTERN)
    title: str
    initial_input: str | None = Field(
        default=None,
        description="If present, the Summarizer may classify the challenge into a template once.",
    )
    template: str | None = Field(
        default=None, description="Explicit template; suppresses auto-classification."
    )


class CreateChallengeResponse(_Model):
    challenge_id: str
    revision: int
    template: str
    template_version: int


class MemoryReadResponse(_Model):
    revision: int
    challenge: ChallengeMeta
    fact: dict[str, list[FactItem]] = Field(default_factory=dict)
    assumption: dict[str, list[AssumptionItem]] = Field(default_factory=dict)
    guessing: GuessingState | None = None


class QueryRequest(_Model):
    question: str = Field(min_length=1)
    layers: list[MemoryLayer] = Field(default_factory=lambda: _DEFAULT_QUERY_LAYERS.copy())


class QueryResponse(_Model):
    """Answers are grounded strictly in current whiteboard state.

    ``references`` is filtered against the live snapshot, so a hallucinated ID can never
    reach the client.
    """

    answer: str
    references: list[str] = Field(default_factory=list)
    revision: int


class GuessRequest(_Model):
    count: int = Field(default=3, ge=1, le=50)
    only_untried: bool = True
    seed: int | None = Field(
        default=None, description="Fixing the seed makes ablation runs reproducible."
    )


class GuessCandidate(_Model):
    id: str
    title: str
    tags: list[str] = Field(default_factory=list)
    tried: bool = False


class GuessResponse(_Model):
    catalog: str
    revision: int
    candidates: list[GuessCandidate]


class IngestResponse(DiffEnvelope):
    source_id: str = Field(description="Journal ID of the record just appended.")


class ErrorBody(_Model):
    code: str
    message: str
    details: dict[str, object] | None = None


class ErrorResponse(_Model):
    error: ErrorBody
