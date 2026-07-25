"""The service layer: every business rule in Memory Core lives here.

The HTTP layer above is a translation shim, and the store/journal below are dumb
persistence.  Anything that decides *what is true* or *who may change it* belongs in this
module, which is also why the whole system can be exercised in tests without a server.

The concurrency discipline is the part worth reading carefully (設計規劃書 §10.4)::

    append the input to the journal          <- happens first, unconditionally
    read a snapshot at revision R            <- short lock
    release the lock
    call the Summarizer                      <- NO LOCK HELD, this is the slow part
    build and validate a proposal(base=R)
    acquire the commit lock
        if current revision == R: validate and atomically commit
        else: release, re-read, re-summarize, bounded retry

A human ``POST /mutate`` therefore never waits behind an in-flight LLM call, which is the
whole point: a person must be able to correct the whiteboard while an agent is running.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from pathlib import Path

from .diff import compute_diff
from .errors import ChallengeNotFound, InvalidRequest, RevisionConflict
from .ids import IdAllocator
from .journal import Journal, utc_now
from .locks import LockRegistry
from .models import (
    GENERAL_SECTION,
    AddOperation,
    AssumptionItem,
    ChallengeMeta,
    CreateChallengeRequest,
    CreateChallengeResponse,
    DemoteOperation,
    DiffEnvelope,
    FactItem,
    GuessCandidate,
    GuessingTechniqueState,
    GuessRequest,
    GuessResponse,
    IngestEnvelope,
    IngestResponse,
    MarkTriedOperation,
    MemoryItem,
    MemoryMutation,
    MemoryReadResponse,
    MemorySnapshot,
    MoveOperation,
    PromoteOperation,
    ProposedItem,
    ProposedUpdate,
    QueryRequest,
    QueryResponse,
    RemoveOperation,
    ReplaceOperation,
    SnapshotMeta,
    SourceRecord,
    SummaryProposal,
    TemplateChangeRequest,
    TemplateDefinition,
)
from .store import StateStore
from .summarizer import Summarizer, enforce_epistemic_policy, evidence_resolver, is_direct_evidence
from .templates import TemplateRegistry, empty_layout, initial_guessing, reclassify

#: How many times ingest may re-summarize when another writer wins the commit race.
DEFAULT_MAX_REBASE = 2


class MemoryService:
    """Owns the whiteboard.  The only component permitted to write ``state.yaml``."""

    def __init__(
        self,
        *,
        store: StateStore,
        journal: Journal,
        templates: TemplateRegistry,
        summarizer: Summarizer,
        max_rebase: int = DEFAULT_MAX_REBASE,
    ) -> None:
        self.store = store
        self.journal = journal
        self.templates = templates
        self.summarizer = summarizer
        self.max_rebase = max_rebase

    # -- construction ------------------------------------------------------

    @classmethod
    def build(
        cls,
        *,
        runs_dir: Path,
        templates_dir: Path,
        summarizer: Summarizer,
        max_rebase: int = DEFAULT_MAX_REBASE,
    ) -> MemoryService:
        """Wire the store and journal onto a *shared* lock registry.

        Sharing matters: a mutation appends to the journal from inside the commit
        critical section, and the re-entrant per-challenge lock is what makes that safe.
        """

        locks = LockRegistry()
        return cls(
            store=StateStore(runs_dir, locks=locks),
            journal=Journal(runs_dir, locks=locks),
            templates=TemplateRegistry.from_dir(templates_dir),
            summarizer=summarizer,
            max_rebase=max_rebase,
        )

    # -- challenges --------------------------------------------------------

    def create_challenge(self, request: CreateChallengeRequest) -> CreateChallengeResponse:
        """Create a challenge at revision 0.

        ``initial_input`` is used for *classification* and is preserved in the journal,
        but it is deliberately **not** summarized here: creation always yields revision 0
        and an empty whiteboard.  Extracting memory from it requires an explicit ingest,
        which keeps "what created the challenge" and "what changed the memory" separable.
        """

        if request.template is not None:
            template = self.templates.get(request.template)
        elif request.initial_input:
            proposal = self.summarizer.classify(
                initial_input=request.initial_input, templates=self.templates.all()
            )
            try:
                template = self.templates.get(proposal.template)
            except Exception:
                template = self.templates.get("generic")
        else:
            template = self.templates.get("generic")

        facts, assumptions = empty_layout(template)
        snapshot = MemorySnapshot(
            revision=0,
            challenge=ChallengeMeta(
                id=request.id,
                title=request.title,
                category=template.id,
                template=template.id,
                template_version=template.version,
            ),
            fact=facts,
            assumption=assumptions,
            guessing=initial_guessing(template),
            meta=SnapshotMeta(last_updated_at=utc_now()),
        )
        self.store.init(snapshot)
        self.journal.ensure(request.id)

        if request.initial_input:
            self.journal.append_input(
                request.id,
                IngestEnvelope.model_validate(
                    {
                        "source": {"type": "human", "actor": "creator"},
                        "input": {"type": "note", "content": request.initial_input},
                    }
                ),
            )

        return CreateChallengeResponse(
            challenge_id=request.id,
            revision=0,
            template=template.id,
            template_version=template.version,
        )

    def list_challenges(self) -> list[str]:
        return self.store.list_challenges()

    # -- ingest ------------------------------------------------------------

    def ingest(self, challenge_id: str, envelope: IngestEnvelope) -> IngestResponse:
        """Journal the input, summarize it without holding a lock, then commit."""

        if not self.store.exists(challenge_id):
            raise ChallengeNotFound(
                f"challenge {challenge_id!r} not found", challenge_id=challenge_id
            )

        # Source record first: a Summarizer failure must never lose the raw input.
        record = self.journal.append_input(challenge_id, envelope)

        last_conflict: RevisionConflict | None = None
        for _ in range(self.max_rebase + 1):
            snapshot = self.store.load(challenge_id)
            base_revision = snapshot.revision
            template = self.templates.get(snapshot.challenge.template)

            # --- slow path, no lock held ---
            raw = self.summarizer.summarize(state=snapshot, source_record=record, template=template)
            records = self.journal.records(challenge_id)
            policy = enforce_epistemic_policy(
                raw,
                snapshot=snapshot,
                is_direct=evidence_resolver(records),
                known_source_ids={r.id for r in records},
            )
            proposal = policy.proposal

            if proposal.is_noop():
                # Nothing worth storing. The journal still has the input; no revision burn.
                return IngestResponse(
                    revision_before=base_revision,
                    revision_after=base_revision,
                    summary=_summary_text(proposal, policy.notes),
                    diff=compute_diff(snapshot, snapshot),
                    source_id=record.id,
                )

            # Bind this attempt's proposal/template explicitly: each retry re-summarizes
            # against a fresh snapshot and must commit its own result, not a later one's.
            def apply(
                state: MemorySnapshot,
                proposal: SummaryProposal = proposal,
                template: TemplateDefinition = template,
            ) -> MemorySnapshot:
                return _apply_proposal(state, proposal, record, template)

            try:
                before, after = self.store.commit(challenge_id, base_revision, apply)
            except RevisionConflict as conflict:
                # Someone else committed while we were summarizing. Re-read and retry.
                last_conflict = conflict
                continue

            return IngestResponse(
                revision_before=before.revision,
                revision_after=after.revision,
                summary=_summary_text(proposal, policy.notes),
                diff=compute_diff(before, after),
                source_id=record.id,
            )

        assert last_conflict is not None
        raise last_conflict

    # -- reads -------------------------------------------------------------

    def read_memory(
        self, challenge_id: str, *, layer: str | None = None, section: str | None = None
    ) -> MemoryReadResponse:
        """Deterministic filtered read.  No model is involved."""

        snapshot = self.store.load(challenge_id)
        if layer not in (None, "fact", "assumption", "guessing"):
            raise InvalidRequest(f"unknown layer {layer!r}", layer=layer)

        facts = snapshot.fact if layer in (None, "fact") else {}
        assumptions = snapshot.assumption if layer in (None, "assumption") else {}
        if section is not None:
            facts = {k: v for k, v in facts.items() if k == section}
            assumptions = {k: v for k, v in assumptions.items() if k == section}

        return MemoryReadResponse(
            revision=snapshot.revision,
            challenge=snapshot.challenge,
            fact=facts,
            assumption=assumptions,
            guessing=snapshot.guessing if layer in (None, "guessing") else None,
        )

    def query(self, challenge_id: str, request: QueryRequest) -> QueryResponse:
        """Natural-language read.

        The model may phrase the answer, but it cannot introduce content: references are
        intersected with the live snapshot, so a hallucinated ID is dropped before the
        client ever sees it.
        """

        snapshot = self.store.load(challenge_id)
        proposal = self.summarizer.answer(
            state=snapshot, question=request.question, layers=request.layers
        )
        valid = snapshot.all_ids()
        references = [ref for ref in dict.fromkeys(proposal.references) if ref in valid]
        return QueryResponse(
            answer=proposal.answer, references=references, revision=snapshot.revision
        )

    def guess(self, challenge_id: str, request: GuessRequest) -> GuessResponse:
        """Sample from the template's curated catalog.

        Fully deterministic given a seed, and never model-driven: the whole point of
        Guessing is that it is a *curated procedural prior*, not brainstorming.  An
        ablation run pinned to ``seed`` reproduces exactly.
        """

        snapshot = self.store.load(challenge_id)
        template = self.templates.resolve(snapshot.guessing.catalog)
        local = snapshot.guessing.state

        pool = sorted(template.guessing, key=lambda t: t.id)
        if request.only_untried:
            pool = [t for t in pool if not local.get(t.id, GuessingTechniqueState()).tried]

        rng = random.Random(request.seed)
        chosen = pool if len(pool) <= request.count else rng.sample(pool, request.count)

        return GuessResponse(
            catalog=snapshot.guessing.catalog,
            revision=snapshot.revision,
            candidates=[
                GuessCandidate(
                    id=t.id,
                    title=t.title,
                    tags=t.tags,
                    tried=local.get(t.id, GuessingTechniqueState()).tried,
                )
                for t in chosen
            ],
        )

    # -- writes ------------------------------------------------------------

    def mutate(self, challenge_id: str, mutation: MemoryMutation) -> DiffEnvelope:
        """Controlled human mutation with optimistic concurrency.

        The journal record is written *inside* the commit critical section, after the
        revision check has passed, so a rejected 409 never leaves a phantom override in
        the evidence log.
        """

        if not self.store.exists(challenge_id):
            raise ChallengeNotFound(
                f"challenge {challenge_id!r} not found", challenge_id=challenge_id
            )
        template = self.templates.get(self.store.load(challenge_id).challenge.template)

        def apply(state: MemorySnapshot) -> MemorySnapshot:
            record = self.journal.append_mutation(challenge_id, mutation)
            return _apply_mutation(state, mutation, record, template)

        before, after = self.store.commit(challenge_id, mutation.expected_revision, apply)
        return DiffEnvelope(
            revision_before=before.revision,
            revision_after=after.revision,
            summary=mutation.reason,
            diff=compute_diff(before, after),
        )

    def set_template(self, challenge_id: str, request: TemplateChangeRequest) -> DiffEnvelope:
        """Explicit reclassification.

        Never happens automatically: once a challenge has a template, ingest leaves it
        alone.  Changing the layout must not change what is believed to be true, so IDs
        and epistemic levels survive untouched (設計規劃書 §7.3).
        """

        if not self.store.exists(challenge_id):
            raise ChallengeNotFound(
                f"challenge {challenge_id!r} not found", challenge_id=challenge_id
            )
        template = self.templates.get(request.template)

        def apply(state: MemorySnapshot) -> MemorySnapshot:
            self.journal.append_template_change(
                challenge_id,
                actor=request.actor,
                reason=request.reason,
                from_catalog=f"{state.challenge.template}@{state.challenge.template_version}",
                to_catalog=template.catalog,
            )
            updated = reclassify(state, template)
            updated.meta.last_updated_at = utc_now()
            return updated

        before, after = self.store.commit(challenge_id, request.expected_revision, apply)
        return DiffEnvelope(
            revision_before=before.revision,
            revision_after=after.revision,
            summary=request.reason,
            diff=compute_diff(before, after),
        )


# ---------------------------------------------------------------------------
# Proposal application
# ---------------------------------------------------------------------------


def _summary_text(proposal: SummaryProposal, notes: Sequence[str]) -> str | None:
    parts = [p for p in [proposal.note] if p]
    parts.extend(notes)
    return " | ".join(parts) if parts else None


def _section_for(template: TemplateDefinition, layer: str, requested: str | None) -> str:
    """Map a requested section onto the template layout, falling back to ``general``."""

    allowed = template.fact_sections if layer == "fact" else template.assumption_sections
    if requested and requested in allowed:
        return requested
    return GENERAL_SECTION if GENERAL_SECTION in allowed else allowed[0]


def _new_item(
    layer: str,
    *,
    item_id: str,
    text: str,
    evidence: list[str],
    tags: list[str],
    actor: str | None,
    now: str,
) -> MemoryItem:
    """Build a Fact or an Assumption with identical bookkeeping.

    One constructor for both layers keeps a promotion from silently gaining or losing
    metadata just because it changed which map it lives in.
    """

    cls = FactItem if layer == "fact" else AssumptionItem
    return cls(
        id=item_id,
        text=text,
        evidence=evidence,
        tags=tags,
        source_actor=actor,
        created_at=now,
        updated_at=now,
    )


def _pop(state: MemorySnapshot, item_id: str) -> tuple[str, str, MemoryItem] | None:
    """Remove an item wherever it lives and return ``(layer, section, item)``."""

    for layer, container in (("fact", state.fact), ("assumption", state.assumption)):
        for section, items in container.items():
            for index, item in enumerate(items):
                if item.id == item_id:
                    return layer, section, items.pop(index)
    return None


def _insert(state: MemorySnapshot, layer: str, section: str, item: MemoryItem) -> None:
    if layer == "fact":
        state.fact.setdefault(section, []).append(
            item if isinstance(item, FactItem) else FactItem(**item.model_dump())
        )
    else:
        state.assumption.setdefault(section, []).append(
            item if isinstance(item, AssumptionItem) else AssumptionItem(**item.model_dump())
        )


def _apply_update(state: MemorySnapshot, update: ProposedUpdate, now: str) -> None:
    found = _pop(state, update.id)
    if found is None:
        return
    layer, section, item = found
    if update.text:
        item.text = update.text
    for evidence_id in update.evidence_add:
        if evidence_id not in item.evidence:
            item.evidence.append(evidence_id)
    item.updated_at = now
    _insert(state, layer, update.section or section, item)


def _apply_proposal(
    state: MemorySnapshot,
    proposal: SummaryProposal,
    record: SourceRecord,
    template: TemplateDefinition,
) -> MemorySnapshot:
    """Turn a policy-checked proposal into a new snapshot.

    IDs are allocated here — never by the model — from one allocator for the whole batch,
    so several additions in a single proposal cannot collide.
    """

    now = utc_now()
    allocator = IdAllocator(state)

    def add(layer: str, proposed: ProposedItem) -> None:
        _insert(
            state,
            layer,
            _section_for(template, layer, proposed.section),
            _new_item(
                layer,
                item_id=allocator.next_for_layer(layer),
                text=proposed.text,
                evidence=list(proposed.evidence),
                tags=list(proposed.tags),
                actor=record.actor,
                now=now,
            ),
        )

    for proposed in proposal.fact_add:
        add("fact", proposed)
    for proposed in proposal.assumption_add:
        add("assumption", proposed)

    for update in proposal.fact_update:
        _apply_update(state, update, now)
    for update in proposal.assumption_update:
        _apply_update(state, update, now)

    for item_id in proposal.assumption_remove:
        _pop(state, item_id)

    for promotion in proposal.promote:
        found = _pop(state, promotion.id)
        if found is None:
            continue
        _, section, item = found
        for evidence_id in promotion.evidence:
            if evidence_id not in item.evidence:
                item.evidence.append(evidence_id)
        item.updated_at = now
        _insert(state, "fact", _section_for(template, "fact", promotion.section or section), item)

    for demotion in proposal.demote:
        found = _pop(state, demotion.id)
        if found is None:
            continue
        _, section, item = found
        item.updated_at = now
        _insert(
            state,
            "assumption",
            _section_for(template, "assumption", demotion.section or section),
            item,
        )

    if proposal.challenge_summary is not None:
        state.challenge.summary = proposal.challenge_summary

    state.meta.last_input_id = record.id
    state.meta.last_updated_at = now
    return state


def _apply_mutation(
    state: MemorySnapshot,
    mutation: MemoryMutation,
    record: SourceRecord,
    template: TemplateDefinition,
) -> MemorySnapshot:
    """Apply controlled operations.

    A human override is a legitimate route from Assumption to Fact — unlike the model's,
    it does not require new evidence, because the human is themselves the evidence.  What
    it does require is attribution: actor and reason are mandatory and land in the
    journal, so the whiteboard can always answer "who decided this, and why".
    """

    now = utc_now()
    allocator = IdAllocator(state)

    for operation in mutation.operations:
        if isinstance(operation, AddOperation):
            _insert(
                state,
                operation.layer,
                _section_for(template, operation.layer, operation.section),
                _new_item(
                    operation.layer,
                    item_id=allocator.next_for_layer(operation.layer),
                    text=operation.text,
                    # The mutation record itself is citable evidence for what it created.
                    evidence=[*operation.evidence, record.id],
                    tags=list(operation.tags),
                    actor=mutation.actor,
                    now=now,
                ),
            )

        elif isinstance(operation, ReplaceOperation):
            found = _pop(state, operation.id)
            if found is None:
                raise InvalidRequest(f"unknown memory id {operation.id!r}", id=operation.id)
            layer, section, item = found
            item.text = operation.text
            if operation.evidence is not None:
                item.evidence = list(operation.evidence)
            if operation.tags is not None:
                item.tags = list(operation.tags)
            item.updated_at = now
            _insert(state, layer, section, item)

        elif isinstance(operation, RemoveOperation):
            if _pop(state, operation.id) is None:
                raise InvalidRequest(f"unknown memory id {operation.id!r}", id=operation.id)

        elif isinstance(operation, PromoteOperation):
            found = _pop(state, operation.id)
            if found is None:
                raise InvalidRequest(f"unknown memory id {operation.id!r}", id=operation.id)
            layer, section, item = found
            if layer == "fact":
                raise InvalidRequest(f"{operation.id} is already a fact", id=operation.id)
            item.evidence.append(record.id)
            item.updated_at = now
            _insert(
                state, "fact", _section_for(template, "fact", operation.section or section), item
            )

        elif isinstance(operation, DemoteOperation):
            found = _pop(state, operation.id)
            if found is None:
                raise InvalidRequest(f"unknown memory id {operation.id!r}", id=operation.id)
            layer, section, item = found
            if layer == "assumption":
                raise InvalidRequest(f"{operation.id} is already an assumption", id=operation.id)
            item.evidence.append(record.id)
            item.updated_at = now
            _insert(
                state,
                "assumption",
                _section_for(template, "assumption", operation.section or section),
                item,
            )

        elif isinstance(operation, MoveOperation):
            found = _pop(state, operation.id)
            if found is None:
                raise InvalidRequest(f"unknown memory id {operation.id!r}", id=operation.id)
            layer, _, item = found
            item.updated_at = now
            _insert(state, layer, _section_for(template, layer, operation.section), item)

        elif isinstance(operation, MarkTriedOperation):
            if operation.technique not in {t.id for t in template.guessing}:
                raise InvalidRequest(
                    f"unknown guessing technique {operation.technique!r}",
                    technique=operation.technique,
                    catalog=template.catalog,
                )
            state.guessing.state[operation.technique] = GuessingTechniqueState(
                tried=operation.tried, note=operation.note
            )

    state.meta.last_updated_at = now
    return state


__all__ = ["MemoryService", "is_direct_evidence"]
