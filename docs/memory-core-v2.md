# Memory Core v2 — Architecture

> Status: implemented (MVP), contract v0 frozen
> Scope: Person 1 — `memory_core/`, `spec/`, this document
> Companion documents: `CTF共享記憶系統_v2_設計規劃書.md` (design), `CTF共享記憶系統_v2_共通設計起點.md` (three-person contract), `../spec/protocol.md` (normative protocol)

---

## 1. Executive summary

Memory Core is a **shared CTF whiteboard service**. Humans and external agents are HTTP
clients. The only model-driven component inside it is a **Summarizer**.

It owns exactly one thing well: *what is currently known about a challenge, how confident
we are, and where that came from.* It records raw inputs to an append-only journal,
summarizes them into two epistemic layers (Fact and Assumption), exposes a third,
template-owned procedural layer (Guessing), and returns a machine-readable semantic diff
for every change.

It is deliberately **not** an agent framework. There is no orchestration, no exploit
loop, no shell execution, no terminal rendering, and no sandbox. Those belong to the
Human Client (Person 2) and the Agent Bridge (Person 3), which reach Memory Core only
through its API.

One invariant holds the design together:

> **Memory Core is the only writer of `state.yaml`.**

Everything else — revision semantics, the commit critical section, the mutation API, the
absence of a YAML patch endpoint — exists to make that invariant enforceable while a human
edits the board during an agent run.

**Implemented and passing:** typed domain model, YAML store with atomic writes and
optimistic concurrency, append-only journal, template registry and reclassification,
Summarizer protocol with a deterministic offline fake and an AIS3 provider, epistemic
policy gate, semantic diff, deterministic guessing, controlled mutation, seven-endpoint
FastAPI surface, generated `spec/`, and 163 offline tests including the agreed end-to-end
fixture.

---

## 2. What changed from v1

**There is no v1 code in this repository.** The repo contained only the two design
documents when this work started (`git ls-tree -r HEAD` = two files). The prompt driving
this task described an existing `ctf-player` tree — `board.py`, `rawstore.py`, `digest.py`,
`digest_gateway.py`, `hooks.py`, `player.py`, `playbooks/`, jailed-agent scripts — none of
which exist here. So there was nothing to inventory, nothing to preserve, and no legacy
tests to run.

The v1 → v2 mapping below is therefore recorded as **design history**, taken from
設計規劃書 §22, not as a migration performed against real files:

| v1 concept | v2 |
|---|---|
| Board `state.yaml` | still exists, restructured into a three-layer semantic whiteboard |
| `facts` | `fact` |
| `hypotheses` | `assumption` |
| playbooks | partly template Guessing catalogs; agent skills move to the Agent Bridge |
| rawstore | simplified into `journal.jsonl` source records |
| digest / digest gateway | removed |
| rolling context | removed |
| hooks | not used by Memory Core |
| Opus commander / autonomous player | moves to the external Agent Bridge |
| resume Board | replaced by the central memory API |
| humans editing YAML directly | replaced by the mutation API, to avoid races |
| jailed agent orchestration | outside Memory Core entirely |

The substantive change is one of *decoupling*. v1 fused observation, epistemic state,
procedural priors and execution context into one autonomous agent, which made every
research variable move together. v2 separates them so each can be measured, replaced and
ablated independently.

---

## 3. Goals and non-goals

### Goals

* One shared memory for a challenge, usable by a human and an agent at the same time.
* A defensible distinction between what is **known** and what is **suspected**.
* Raw inputs preserved verbatim and citable.
* Deterministic, reproducible behaviour everywhere a model is not strictly required.
* A frozen contract that lets three people build in parallel from day one.
* Safe concurrent editing without a database.

### Non-goals (enforced, not aspirational)

SQLite, Redis, vector DB, embeddings, knowledge graph, RDF, event-sourcing framework,
message broker, multi-agent coordinator, YOLO loop, Claude session orchestration, PTY
recorder, WebSocket collaboration, multi-process writers, distributed locking, RBAC,
cryptographic signatures, arbitrary YAML patch API, automatic importance scoring,
autonomous memory-graph evolution, web GUI.

None of these are present. If one becomes genuinely necessary, the rule agreed by all
three developers is: write an ADR describing the observed problem first — do not implement
it and document it afterwards.

---

## 4. Research grounding

Each paper contributes one idea. We take that idea and explicitly reject the machinery
around it. Where a choice is ours rather than a paper's, it is labelled **[project
choice]**.

### CoALA — *Cognitive Architectures for Language Agents*
<https://arxiv.org/abs/2309.02427>

**Paper:** distinguishes working / episodic / semantic / procedural memory for language
agents.

**Borrowed:** only the declarative-vs-procedural split. Fact and Assumption are
challenge-specific declarative state; the template's Guessing catalog is a procedural
prior — a set of things one *could* try, not things one knows. Journal records are
episodic source material.

**Rejected:** the full cognitive architecture. We are not building a CoALA agent, and
Memory Core has no decision procedure at all.

### A-MEM — *Agentic Memory for LLM Agents*
<https://arxiv.org/abs/2502.12110>

**Paper:** structured atomic notes with metadata, links, and autonomous memory evolution.

**Borrowed:** atomic items, stable identity, light metadata, and the principle that new
information may revise an existing item rather than only append a new one.

**Rejected:** vector database, embedding retrieval, graph database, autonomous global
linking, and bulk automatic rewriting of stored history. Retrieval here is a dictionary
lookup and a section filter, because at CTF scale that is sufficient and it is auditable.

### Generative Agents — *Interactive Simulacra of Human Behavior*
<https://arxiv.org/abs/2304.03442>

**Paper:** a memory stream feeding reflection and planning.

**Borrowed:** one principle — a derived summary must never replace the raw experience it
came from. Hence the journal is written **before** the Summarizer runs, and `state.yaml`
never becomes the only copy of what happened.

**Rejected:** reflection trees, importance scoring, planning. **[project choice]** No
automatic importance score exists; adding one without a concrete tested requirement would
just be an unfalsifiable number in the file.

### LongMemEval — *Benchmarking Chat Assistants on Long-Term Interactive Memory*
<https://arxiv.org/abs/2410.10813>

**Paper:** evaluates knowledge update and abstention as first-class capabilities.

**Borrowed:** both, as hard requirements. New evidence may revise an earlier
understanding (`fact_update`, `assumption_update`, `promote`, `demote`). Insufficient
evidence must **not** be promoted into certainty — the policy gate downgrades an
unsupported Fact proposal to an Assumption rather than accepting it.

**Rejected:** the benchmark itself; we are not scoring against it.

### CHAP — *Collaborative Human-Agent Protocol*
<https://arxiv.org/abs/2606.09751>

**Paper:** a proposed protocol for human-agent shared workspaces: attribution, append-only
evidence, override, diff, handoff.

**Borrowed:** actor attribution, the append-only source record, mandatory human-override
rationale, explicit revisions, and semantic diff visibility.

**Rejected:** CHAP itself. **[project note]** This is treated as a recent proposal rather
than a mature standard, exactly as the design documents frame it. The citation is carried
over from those documents and has not been independently verified from this environment;
worth confirming before it appears in anything published.

---

## 5. Fact / Assumption / Guessing

Three concepts. Not four, not two.

### Fact

An atomic, challenge-specific statement confirmed by **direct evidence** or by an
**explicit human override**.

Acceptable: a port shown open by a scan; `checksec` output; file contents; observed HTTP
behaviour; a reproducible exploit result; a human stating they verified something and
saying why.

Not acceptable: an external agent saying "this is probably SQL injection." That is not
evidence, regardless of how confident the sentence sounds.

### Assumption

An atomic, challenge-specific, plausible, testable proposition that is **not yet
sufficiently verified**. Agent inferences, ordinary human hypotheses, and attack paths
synthesised from several observations all live here.

### Guessing

A **curated technique catalog supplied by the template** — `WEB-SQLI`, `WEB-SSRF`,
`PWN-FMTSTR`, `PWN-RETLIBC`. It is a procedural prior, not brainstorming, and the
Summarizer cannot write to it.

`state.yaml` stores only the catalog identity and per-challenge local state:

```yaml
guessing:
  catalog: web@1
  state:
    WEB-SQLI: {tried: true}
    WEB-SSRF: {tried: false}
```

Full technique metadata is always resolved from the template registry.

### The permitted path

```
Guessing technique
   -> concrete challenge hypothesis
   -> Assumption
   -> direct evidence
   -> Fact
```

Forbidden, and structurally prevented:

```
Guessing -> "the model thinks it sounds right" -> Fact
```

---

## 6. The source journal

`runs/<challenge-id>/journal.jsonl`, append-only, one JSON object per line, fsynced.

```json
{"id":"I-83b70a9a","type":"input","at":"...","actor":"ctshen","source":{...},"input_type":"note","content":{...}}
{"id":"I-42aa8e92","type":"input","at":"...","actor":"agent","input_type":"run_log","content":{"events":[...]}}
{"id":"M-c420e4e1","type":"mutation","at":"...","actor":"ctshen","reason":"Verified in GDB","operations":[...]}
```

* IDs are `I-<8 hex>` for inputs and `M-<8 hex>` for mutations, derived deterministically
  from `(sequence, payload)` so that journal replay — the mechanism the ablation harness
  uses to compare Summarizers on identical inputs — is reproducible.
* Fact and Assumption entries cite these IDs in `evidence`.
* The journal is **not a fourth memory layer** and state is **never** rebuilt from it. It
  is not an event-sourcing log, and there is no framework behind it.
* An input is journalled **before** summarization, so a Summarizer failure loses nothing.
* A mutation is journalled **inside the commit critical section, after the revision check
  passes and after the operations have been rehearsed against a copy of the state**. A
  record therefore means *this happened*, never *this was attempted and refused* — neither
  a stale revision (409) nor an operation naming something that does not exist (400) can
  leave a phantom override in the evidence log. The rehearsal runs the real applier rather
  than a parallel list of preconditions, which would be free to drift from the operations
  it claims to check.

---

## 7. State schema

`runs/<challenge-id>/state.yaml`. Canonical definition: `MemorySnapshot` in
`memory_core/models.py`; published JSON Schema: `spec/memory.schema.json`.

```yaml
schema_version: 2
revision: 17

challenge:
  id: silver-bullet
  title: Silver Bullet
  category: pwn
  template: pwn
  template_version: 1
  summary: 32-bit ELF challenge. Current work focuses on ...

fact:
  binary:
    - id: F001
      text: The binary is ELF i386.
      evidence: [I-83b70a9a]
      source_actor: agent
      tags: []
      created_at: "2026-07-25T..."
      updated_at: "2026-07-25T..."
  progress: []

assumption:
  exploit_path:
    - id: A001
      text: power_up() may enable a memory-corruption primitive.
      evidence: [I-42aa8e92]
  next_checks: []

guessing:
  catalog: pwn@1
  state:
    PWN-FMTSTR: {tried: true}
    PWN-RETLIBC: {tried: false}

meta:
  last_input_id: I-42aa8e92
  last_updated_at: "2026-07-25T..."
```

Notes:

* Sections declared by the template are materialised even when empty. **[project
  choice]** This tells the Summarizer and the Human Client what the available layout is
  without a second lookup.
* `None` values are pruned on write for readability; every optional field has a default, so
  a pruned document round-trips to an identical model.
* Metadata is intentionally minimal. **[project choice]** There is no confidence score —
  adding a number nobody tests against would be worse than the two honest layers.

### Challenge ids are names, not paths

A challenge id becomes a directory name under `runs/`, so `CHALLENGE_ID_PATTERN` in
`models.py` restricts the first character specifically to rule out `.` and `..`. Both look
like ordinary ids and both resolve outside the runs directory — `..` produced a challenge
whose `state.yaml` landed in the *parent* of `runs/` and which `GET /v1/challenges` could
then never list.

The rule is enforced twice on purpose. The request model carries it so it appears in
`spec/openapi.yaml` and clients can see it, and `store.require_safe_challenge_id` re-checks
at every point a filesystem path is built — because a challenge id also arrives as a **URL
path parameter**, where body validation never runs. Guarding path construction rather than
each endpoint means a new endpoint cannot forget.

### Identity

IDs are `F001` / `A001`, assigned by Memory Core, never by a client and never by the model.

**[project choice]** The `F`/`A` prefix always matches the layer the item currently lives
in. An ID is stable for as long as the item stays in its layer — section moves, text and
evidence updates, and template reclassification all preserve it — but **crossing layers
mints a new ID**. Promoting `A017` produces a new fact `F00N` carrying
`promoted_from: "A017"`; demoting is the mirror image. Follow the `promoted_from` chain to
reconstruct an item's epistemic history.

**An ID is never reused**, even after its item is removed or promoted away. This is a
stronger promise than "unique among current items" and it is the one that matters: a reused
ID would make the same name refer to two different claims across the journal's history,
silently invalidating every evidence reference and diff minted under the old name. Keeping
it requires persisted counters, since scanning only live items would happily reissue `F001`
after the original was deleted — hence `meta.fact_seq` and `meta.assumption_seq`, which
only ever go up. The scan over live IDs and over `promoted_from` ancestry is retained as a
floor, so a hand-edited `state.yaml` with a stale counter still cannot collide.

The alternative — preserving the ID across a promotion, so that `A017` lives on inside the
`fact` map — was considered and rejected: it makes the prefix mean "where this was first
created", which every client then has to know not to trust. See §24 (ADR-1).

---

## 8. Template contract

```yaml
id: web
version: 1
description: Web security challenge
classifier_hints: [HTTP, cookie, API, login]
fact_sections: [target, endpoints, technologies, authentication, validated_behavior, progress, general]
assumption_sections: [attack_surface, vulnerability_hypotheses, attack_paths, next_checks, general]
guessing:
  - id: WEB-SQLI
    title: SQL Injection
    tags: [database, input]
```

Invariants, validated at load time:

* `fact_sections` **must** contain `general`; `assumption_sections` **must** contain
  `general`. Without them reclassification could have nowhere to put an item.
* Section names within a list are unique.
* Guessing technique IDs are unique and stable within a template version.
* A `generic` template must exist, as the classification fallback.

Ownership: Memory Core owns the schema, the loader, and the reclassification algorithm.
**Person 3 owns the real technique content.** `templates/generic.yaml` and
`templates/web.yaml` shipped here are minimal fixtures with a header comment saying so —
`web.yaml` exists specifically so reclassification has a second template to be tested
against, per 共通設計起點 §7.

---

## 9. Classification and reclassification

* A challenge may be classified **once**, at creation, from `initial_input`. If
  classification fails — an unknown template, unparseable output, a backend that is down,
  unreachable or too slow — it falls back to `generic`. An advisory model answer must never
  block challenge creation: the template is a *layout*, not a truth claim, and a wrong one
  is repaired with one `POST /template`, whereas refusing to create the challenge strands
  the human with nowhere to put their notes.
* The single exception is `summarizer_unauthorized`, which is re-raised. A rejected key is
  a server misconfiguration, and swallowing it would create every challenge as `generic`
  indefinitely while looking exactly like a classifier that never matches anything (§12).
* **Normal ingest never auto-reclassifies.** Once the template is set, it stays set.
* Replacing it is an explicit client operation: `POST /v1/challenges/{id}/template`, with
  `expected_revision`, `actor` and `reason`.

Reclassification guarantees, in priority order:

1. every Fact/Assumption ID is preserved;
2. epistemic level is preserved — a Fact stays a Fact;
3. only the section mapping changes, by exact section-name match;
4. anything with no matching target section lands in `general`;
5. the Guessing catalog is replaced wholesale;
6. `tried` state survives for technique IDs still present in the new catalog;
7. a semantic diff is returned, with `template_changed: {from, to}`.

> **Changing the category or the layout is not the same as re-judging what is true.**

---

## 10. Ingest flow

One vendor-neutral envelope for humans and agents:

```json
{"source": {"type": "human", "actor": "ctshen"},
 "input":  {"type": "note", "content": "I manually tested the login flow..."}}
```

```json
{"source": {"type": "human", "actor": "ctshen"},
 "input":  {"type": "command_result", "command": ["nmap","-sV","target"],
            "exit_code": 0, "stdout": "...", "stderr": ""}}
```

```json
{"source": {"type": "agent", "provider": "anthropic", "model": "claude-...",
            "run_id": "run-123", "session_id": "session-abc"},
 "input":  {"type": "run_log", "events": [
   {"seq": 1, "role": "assistant", "kind": "message",     "content": "..."},
   {"seq": 2, "role": "assistant", "kind": "tool_call",   "name": "Bash", "content": "checksec ./chall"},
   {"seq": 3, "role": "tool",      "kind": "tool_result", "content": "NX enabled..."}]}}
```

Claude SDK classes and other provider event objects are **never** part of the domain model.
Person 3 owns `Claude events -> AgentRunEvent -> IngestEnvelope`; Person 2 owns
`human text / process stdout+stderr -> IngestEnvelope`; Memory Core only ever sees the
normalized envelope. Provider metadata (`provider`, `model`, `run_id`, `session_id`) is
retained on the journal record so the same/new-session ablation can be analysed later —
without Memory Core knowing anything about how a provider session works.

Pipeline (`MemoryService.ingest`):

```
append envelope to journal            <- unconditional, before anything can fail
read snapshot at revision R           <- short lock
release
call the Summarizer                   <- NO LOCK HELD
enforce the epistemic policy on the proposal(base = R)
if the proposal is a no-op: return revision_before == revision_after, empty diff
acquire the commit lock
    if current revision == R: apply, revision += 1, atomic write
    else: release, re-read, re-summarize (bounded: 2 retries), then 409
```

**[project choice]** A no-op ingest does not burn a revision. The input is still
journalled; there is simply nothing to commit, and inflating the revision counter would
make "the board changed" indistinguishable from "someone typed something irrelevant".

---

## 11. Summarizer contract

```python
class Summarizer(Protocol):
    def summarize(
        self, *, state: MemorySnapshot, source_record: SourceRecord, template: TemplateDefinition
    ) -> SummaryProposal: ...
    def classify(
        self, *, initial_input: str, templates: Sequence[TemplateDefinition]
    ) -> ClassificationProposal: ...
    def answer(
        self, *, state: MemorySnapshot, question: str, layers: Sequence[str]
    ) -> AnswerProposal: ...
```

**[project choice]** The original brief sketched a single `summarize` method.
Classification and query answering are also model-driven per 設計規劃書 §6.1 items 1 and
5, so they are part of the interface rather than hidden inside `summarize`. Everything
else in Memory Core is deterministic and model-free.

### Epistemic policy

Stated in `memory_core/summarizer.py`, enforced by `enforce_epistemic_policy`, restated
verbatim in the system prompt, and covered by `tests/test_summarizer_policy.py`.

* **R1** — every proposed Fact cites at least one journal source ID that exists.
* **R2** — a Fact requires **direct evidence**:
  * `command_result` → direct
  * `run_log` containing at least one `tool_result` → direct
  * a **human** `note` → direct
  * an **agent** `note`, or a narrative-only `run_log` → **not** direct
* **R3** — insufficient evidence means Assumption, or nothing. A Fact proposal failing
  R1/R2 is **downgraded** to an Assumption; the observation is kept, the certainty is not.
* **R4** — model-initiated promotion requires *new* direct evidence the item did not
  already carry. A human override without new evidence is legitimate, but goes through
  `POST /mutate` with an actor and a reason — not through the model pretending it verified
  something.
* **R5** — the Summarizer can neither touch the Guessing catalog nor delete a Fact. Both
  are structurally impossible: `SummaryProposal` has no `guessing` and no `fact_remove`
  field. A Fact the model now doubts can only be **demoted**, which is visible in the diff.

#### On R2's human-note carve-out

This is the one genuine interpretive decision in the design, and it is worth being
explicit about because the two source documents pull in slightly different directions.

設計規劃書 §6.2 says a human's *speculation* (推測) defaults to Assumption. 共通設計起點
§10 — the agreed integration fixture — has a human note, *"I ran nmap and port 80 is
open"*, producing `F001: TCP/80 is open.` as a **Fact**.

Both are right, because they describe different things. A human note may contain an
**observation** or an **inference**. Only the observation may support a Fact. That
distinction is a judgment call, so it lives in the prompt, where a model can weigh it.

What the code-level gate guarantees instead is the asymmetry the whole design rests on:
**a first-party human report is admissible evidence; an external agent's narrative never
is.** An agent must show a tool result. That is mechanically checkable, and it is the rule
the brief states most emphatically.

`SummaryProposal` deliberately has no `id` field on new items — IDs are allocated by
Memory Core at commit time, so the model cannot invent, collide with, or overwrite an
existing identity even if it tries.

---

## 12. AIS3 backend

Source: <https://github.com/chunying/ais3-ai-infra>

```bash
AIS3_BASE_URL=https://llm-api.zoolab.org/v1
AIS3_API_KEY=...
AIS3_SUMMARIZER_MODEL=ais3/nemotron-3-ultra-550b
AIS3_TIMEOUT=300          # read timeout in seconds; see "Failure modes" below
```

`memory_core/providers/ais3.py` uses only the baseline OpenAI-compatible
`POST /chat/completions` surface at `temperature=0`. **No provider-specific
structured-output extension is assumed.** The contract is: ask for JSON in the prompt,
extract it (tolerating code fences and leading prose), validate against the typed model,
and allow **one bounded repair round-trip** that feeds the validation error back. If that
fails, raise `proposal_invalid` rather than commit something unvalidated.

### Failure modes, measured against the real backend

A live probe of `llm-api.zoolab.org` produced two findings that shaped the error handling.

**Latency is dominated by contention, not by input size.** The same two live tests took
3.2s on one run and 78.4s on the next; a single `summarize` was observed at 57s. The read
timeout is therefore `AIS3_TIMEOUT`, defaulting to a generous **300s**, while the connect
timeout stays at 10s so an unreachable host still fails fast. A long read budget is cheap
here precisely because ingest holds **no lock** during the model call: one slow request
delays that one ingest and nothing else, whereas a tight timeout throws away work that was
about to succeed. A timeout surfaces as `summarizer_unavailable` (502) and says so — the
input is already journalled, so the ingest can simply be retried.

**A rejected key is not an outage.** The backend answers `401` with
`{"error": {"message": "Authentication Error, No api key passed in.", ...}}`. Reporting that
as 502 would invite a retry that can never succeed, and reporting it as 401 would suggest
the *caller* needs to authenticate when in fact the operator of this service has to set
`AIS3_API_KEY`. It therefore maps to a distinct `summarizer_unauthorized` (500), carrying
the upstream message in `error.details`. `classify()` and `answer()` degrade gracefully on
unparseable output but deliberately **do not** swallow this error — otherwise a bad key
would silently create every challenge as `generic` and nobody would notice.

That upstream text is **redacted before it is stored or returned**. LiteLLM echoes the
rejected key and its hash straight back — `Received API Key = sk-...rong, Key Hash (Token)
= 7b31d7a4…` — and that string would otherwise land in an HTTP response body and in logs.
Its own partial redaction is not something to rely on from an arbitrary proxy, so
`sk-*` tokens, long hex digests and `Bearer` values are scrubbed on the way through. The
diagnosis survives; the credential does not.

Secrets live only in the environment; `.env.example` documents the variables and contains
no key.

The default test suite performs **no network call at all** — a socket guard in
`tests/conftest.py` fails any outbound connection, and `tests/test_offline_guarantee.py`
verifies the guard itself bites. The provider's parsing, repair, endpoint and error paths
are covered with an `httpx.MockTransport` double, so a bug there is caught in CI rather
than the first time someone exports a key.

Live check (opt-in only):

```bash
AIS3_BASE_URL=https://llm-api.zoolab.org/v1 \
AIS3_API_KEY=... \
AIS3_SUMMARIZER_MODEL=ais3/nemotron-3-ultra-550b \
AIS3_LIVE_TEST=1 \
.venv/bin/python -m pytest -m live -v
```

---

## 13. API overview

```
POST /v1/challenges                              create at revision 0
GET  /v1/challenges                              list ids
POST /v1/challenges/{id}/ingest                  normalized human or agent input
GET  /v1/challenges/{id}/memory?layer=&section=  deterministic filtered read
POST /v1/challenges/{id}/query                   natural-language read
POST /v1/challenges/{id}/guess                   draw from the template catalog
POST /v1/challenges/{id}/mutate                  controlled human mutation
POST /v1/challenges/{id}/template                explicit reclassification
GET  /healthz
```

Full definition: `spec/openapi.yaml`, generated from the same Pydantic models the service
uses. `spec/protocol.md` carries the semantics a schema cannot express.

**Framework choice.** FastAPI + Pydantic v2, and nothing else. The justification is
narrow: Pydantic gives the single canonical typed definition the shared contract requires,
and FastAPI derives the OpenAPI document from those same types — so the contract Person 2
and Person 3 build against cannot silently drift from the implementation. No database and
no broker. The HTTP handlers are thin `async def` wrappers over the synchronous service;
the service still owns the short per-challenge critical sections, and tests exercise both
the service directly and the ASGI boundary.

**Read** is deterministic and model-free: filter by `layer` and `section`, nothing more.

**Query** may use the Summarizer, but answers only from current whiteboard state, and
returned `references` are intersected with the live snapshot — an invented ID is dropped
before it reaches a client.

**Guess** is `random.Random(seed)` over the resolved catalog, sorted by ID first. Same seed
→ same draw, always. The LLM is never used to invent techniques.

---

## 14. Mutation and human override

```json
{
  "expected_revision": 42,
  "actor": "ctshen",
  "reason": "Verified manually in GDB",
  "operations": [{"op": "promote", "id": "A017", "to": "fact"}]
}
```

`reason` is mandatory (min length 1) and `operations` must be non-empty. Controlled
operations only:

| op | effect |
|---|---|
| `add` | create a Fact or Assumption in a section |
| `replace` | change text / evidence / tags |
| `remove` | delete an item |
| `promote` | Assumption → Fact; new fact ID, `promoted_from` links back |
| `demote` | Fact → Assumption; new assumption ID, `promoted_from` links back |
| `move` | change section only; epistemic level untouched |
| `mark_tried` | flip a Guessing technique's local `tried` flag |

There is **no arbitrary YAML patch endpoint**. An unknown `op` fails schema validation.

**[project choice] `mark_tried` is an addition beyond the six operations in the brief.**
`state.yaml` stores per-challenge `tried` flags (設計規劃書 §4.3) and
`GuessRequest.only_untried` is meaningless without a sanctioned way to set them. It remains
a *controlled* operation over a template-owned identifier: it can flip a flag and attach a
note, and it can neither add a technique to the catalog nor change its metadata. Marking an
unknown technique is a 400.

A human override is the one legitimate route from Assumption to Fact without new evidence —
because the human is themselves the evidence. What it requires is attribution: actor and
reason land in the journal, and the mutation record ID is appended to the item's `evidence`,
so the board can always answer *who decided this, and why*.

---

## 15. Semantic diff

Every successful state-changing operation returns:

```json
{
  "revision_before": 16,
  "revision_after": 17,
  "summary": "The new scan confirmed an HTTP service.",
  "diff": {
    "fact_added": ["F014"], "fact_updated": [], "fact_removed": [],
    "assumption_added": ["A009"], "assumption_updated": [], "assumption_removed": [],
    "promoted": [], "demoted": [], "template_changed": null
  }
}
```

Ingest, mutation and reclassification all funnel through the same `compute_diff(before,
after)`, so a client never has to special-case the operation that produced a diff.

An item crossing layers is reported under its **old** ID in `promoted` (or `demoted`), while
its **new** ID appears as an ordinary `fact_added` / `assumption_added`. The old ID is
deliberately kept out of `*_removed`: the claim did not go away, it changed epistemic
status, and a client rendering removals must not show it as deleted. A section change is an
`*_updated`, not a remove-and-add.

`template_changed` is `null` or `{"from": "generic@1", "to": "web@1"}`.

Rendering is not part of the core. Person 1 produces the diff; Person 2 renders it; Person 3
may use it as loop telemetry but never modifies it.

---

## 16. Concurrency, revisions and atomic writes

### The invariant

**Memory Core is the only writer of `state.yaml`.** Human CLI, TUI, Claude skill and Agent
Bridge all go through the API.

### Optimistic concurrency

Each state carries `revision: N`, starting at 0. Explicit mutations and reclassifications
send `{"expected_revision": N}`; a stale value returns **409** with the current revision in
`error.details`, and the client re-reads and retries.

### Commit critical section

Per challenge, a re-entrant lock held only for the commit:

```
lock -> re-read -> check revision -> apply -> write temp -> fsync -> atomic replace
     -> fsync dir -> revision += 1 -> unlock
```

### No lock during the LLM call

This is the point of the whole design. Ingest reads a snapshot, releases, summarizes, then
re-acquires to commit; if the revision moved it re-reads and re-summarizes, bounded to two
retries. `tests/test_concurrency.py` proves it: a human `mutate` completes in under two
seconds while a deliberately blocked Summarizer is mid-call, and the ingest then rebases
onto the human's write rather than clobbering it.

### Why both mechanisms are required

Atomic rename gives **crash** atomicity — a reader never sees a half-written file. It does
**not** prevent a **logical lost update**: two writers both based on revision 41 each
produce a perfectly valid file, and one silently wins. The revision check *inside* the
critical section is what prevents that. Neither mechanism substitutes for the other.

### MVP process model

One Memory Core process (`uvicorn --workers 1`). No multi-worker, no distributed lock, no
network-filesystem multi-writer, no external DB transaction.

---

## 17. Error model

```json
{"error": {"code": "revision_conflict", "message": "...", "details": {"current_revision": 43}}}
```

| code | status | meaning |
|---|---|---|
| `invalid_request` | 400 | semantically invalid operation (unknown memory ID, unknown technique) |
| `challenge_not_found`, `template_not_found` | 404 | |
| `challenge_exists`, `revision_conflict` | 409 | |
| `proposal_invalid` | 422 | Summarizer output failed schema validation after the repair budget |
| `summarizer_unavailable` | 502 | backend down, unreachable or slower than `AIS3_TIMEOUT` — **retryable**, since the input is already journalled |
| `summarizer_unauthorized` | 500 | the server's AIS3 key was rejected — **not** retryable; deliberately not 502 (which invites a retry that can never work) and not 401 (which would imply the *caller* must authenticate) |
| `state_corrupt`, `invalid_template`, `internal_error` | 500 | |

FastAPI's own request-body validation failures return its standard 422. Domain errors are
raised by the service and translated by a single exception handler; the HTTP layer never
invents a status code.

Corrupt state is **reported, not guessed at**: a `state.yaml` that fails schema validation
raises `state_corrupt` rather than being silently repaired.

---

## 18. Directory layout

```
docs/
  memory-core-v2.md              this document
  CTF共享記憶系統_v2_*.md         the two source design documents

spec/                            the frozen contract (generated + handwritten)
  openapi.yaml                   <- generated from memory_core/api.py + models.py
  memory.schema.json             <- generated from MemorySnapshot
  template.schema.json           <- generated from TemplateDefinition
  protocol.md                    handwritten: semantics a schema cannot express

memory_core/
  models.py       canonical typed contract — the single source of truth
  errors.py       typed domain errors and their HTTP mapping
  config.py       environment settings
  ids.py          memory ID allocation, deterministic journal IDs
  locks.py        per-challenge lock registry
  store.py        state.yaml load / validate / atomic save / commit
  journal.py      append-only journal.jsonl
  templates.py    registry, validation, reclassification
  summarizer.py   Summarizer protocol, epistemic policy gate, FakeSummarizer
  diff.py         semantic diff
  service.py      all business rules
  api.py          thin FastAPI layer
  __main__.py     runner, including --fake-summarizer mock-server mode
  providers/
    ais3.py       OpenAI-compatible AIS3 backend

templates/
  generic.yaml    minimal fixture (Person 3 owns the real content)
  web.yaml        minimal fixture, exists so reclassification is testable

scripts/
  gen_spec.py                    regenerates spec/ ; --check fails on drift
  memctl.py                      smoke-test client -- NOT the Human Client
tests/                            163 offline tests + 2 opt-in live tests
runs/                             per-challenge state (gitignored)
```

Deliberately absent: `cli/` and `agent_bridge/`. Those are Person 2 and Person 3.

---

## 19. Ownership boundaries

| | Memory Core (P1) | Human Client (P2) | Agent Bridge + Templates (P3) |
|---|---|---|---|
| **Owns** | state, journal, template loader/algorithm, Summarizer, diff, revision semantics, HTTP API | note / capture / show / query / guess / modify / template set, diff rendering, JSON output, later a TUI | Claude adapter, event normalization, memory skill, normal & YOLO loops, same/new session, cancel, real CTF catalogs |
| **Must not** | orchestrate agents, render UI, execute shell, author the real technique catalogs | write `state.yaml` directly | write `state.yaml` directly, send provider-specific objects into the core |

### The mock server

Person 2 and Person 3 do not have to wait for anything:

```bash
.venv/bin/python -m memory_core --fake-summarizer --runs ./runs --templates ./templates
```

`FakeSummarizer` is deterministic, needs no API key, makes no network call, and obeys the
same epistemic policy as the real backend — so nobody builds against looser behaviour than
production.

`scripts/memctl.py` drives that server over plain HTTP and imports nothing from
`memory_core`, which is the point: it demonstrates that the contract in `spec/` is
sufficient on its own. It is a **smoke-test client, not the Human Client** — Person 2 owns
`cli/` and the real `ctfmem` UX, and nothing here should be taken as a design for it.

```bash
scripts/memctl.py create demo-web --title "Demo Web"
scripts/memctl.py note demo-web "I ran nmap and port 80 is open."
scripts/memctl.py capture demo-web -- nmap -sV target
scripts/memctl.py show demo-web
scripts/memctl.py query demo-web "which ports are confirmed open?"
scripts/memctl.py guess demo-web --seed 42
scripts/memctl.py promote demo-web A001 --reason "verified in a browser"
scripts/memctl.py template demo-web web --reason "it is a web challenge"
```

Mutating commands re-read the revision first unless `--expected-revision` pins one. That
narrows the lost-update window without closing it — if an agent commits in between, the
server answers 409, which is the contract working rather than a bug.

---

## 20. Testing strategy

163 tests, all offline, ~3 seconds.

| file | covers |
|---|---|
| `test_store.py` | revision 0 on create; valid YAML after atomic save; no temp files left; stale revision conflicts; corrupt state reported; **a path-shaped challenge id (`..`, `.`) is refused by the store, the journal and the request model alike** |
| `test_journal.py` | human input verbatim; normalized agent run log preserved with provider metadata; append-only; unique IDs; input journalled even on a no-op ingest |
| `test_summarizer_policy.py` | what counts as direct evidence; unsupported Facts downgraded; agent narrative cannot create a Fact; agent tool result can; promotion needs new evidence; IDs stable across updates; no auto-reclassification; **an unavailable classifier falls back to `generic` while a rejected key still surfaces** |
| `test_templates.py` | missing `general` rejected; duplicate technique IDs rejected; `generic` fallback required; reclassification preserves IDs, levels and evidence; unmappable entries land in `general`; `tried` carried over; attribution journalled |
| `test_guess.py` | catalog comes from the template; `seed` reproducible; `only_untried`; `mark_tried`; follows the challenge's template; asking for more than exists |
| `test_mutation.py` | reason mandatory; promotion mints a fact ID with a `promoted_from` backlink; **a promoted or removed ID is never handed out again, and the high-water marks survive a reload**; override records actor/reason; move changes only the section; stale revision 409; **no rejected mutation — 409 or 400 — leaves a phantom journal record, a batch is all-or-nothing, and the rehearsal burns no IDs**; no arbitrary patch op |
| `test_concurrency.py` | two clients on one revision — second rejected, no lost update; **human mutation not blocked by an in-flight Summarizer**; bounded rebase then 409 |
| `test_diff.py` | additions / removals / updates / template change; promotion and demotion reported under the **old** ID with the new one as an addition; `promoted_from` alone is not a promotion; `from` alias on the wire; exact frozen field set |
| `test_query.py` | references only existing IDs; **invented references dropped**; abstains on an empty board; layer filter; read-only |
| `test_api.py` | all endpoints via in-process ASGI requests; 400 / 404 / 409 / 422 bodies; published paths; **a path-shaped id is refused in the body (422) and in the URL (400)** |
| `test_spec_drift.py` | `spec/` matches what the code generates; frozen paths and types present; mutation ops are a closed set; shipped templates validate against the published schema |
| `test_offline_guarantee.py` | the network guard actually blocks; AIS3 provider parsing, repair budget, endpoint URL; **a rejected key maps to `summarizer_unauthorized` (500) and is not swallowed by the classifier fallback**; a timeout maps to a retryable 502 naming the budget — all with a transport double |
| `test_memctl.py` | the smoke client's rendering: a promotion shows both ids and no spurious removal, a no-op is not shown as a change, every documented error code carries advice |
| `test_e2e_demo_web.py` | the agreed 共通設計起點 §10 fixture, over HTTP, reloaded from disk |
| `test_ais3_live.py` | opt-in only (`-m live`), skipped without a key |

The end-to-end fixture is the acceptance test for the module split: create `demo-web` →
human note → `F001` + `A001` at revision 1 → read → query → seeded guess → human promotion
→ agent run log at revision 3 → stale mutation 409 → reload from disk, state and journal
both still valid.

---

## 21. Ablation-friendly properties

The architecture makes each research variable independently switchable:

* **Session** — `run_id` / `session_id` / `provider` are recorded but never interpreted, so
  same-session vs new-session is an Agent Bridge variable that Memory Core simply logs.
* **Memory layer** — `GET /memory?layer=` supports no-memory / fact-only /
  fact+assumption / +guessing conditions without touching the core.
* **Template** — `generic` vs category-specific is a one-call switch, and reclassification
  preserves truth, so the same challenge can be re-laid-out mid-experiment.
* **Guessing** — disabled, or seeded and exactly reproducible via `seed`.
* **Human intervention** — none / notes only / mutation+override, all distinguishable in
  the journal by record type and actor.
* **Summarizer** — the journal preserves every input verbatim, so replaying a fixed input
  sequence against a different prompt, model or classification policy is possible without
  re-running any agent.

Determinism is the enabling property here: everything except the three Summarizer calls is
a pure function of state and input.

---

## 22. Migration and relationship to v1

There is no v1 code to migrate (§2). `schema_version: 2` is pinned in the schema and
validated on load, so if a v1 board ever does turn up, it fails fast with `state_corrupt`
rather than being silently misread. No backwards-compatibility shim for `facts` /
`hypotheses` / digest output / rolling-context metadata exists, and none is planned — the
redesign is explicitly allowed to break the old architecture.

---

## 23. Deferred work

Not blocking, and intentionally not started:

* Human CLI/TUI (Person 2) and Agent Bridge (Person 3).
* Real per-category technique catalogs — `pwn`, `rev`, `crypto`, `forensics`, `misc`
  (Person 3).
* A richer classifier. `FakeSummarizer.classify` is keyword counting over
  `classifier_hints`; the AIS3 path is a single prompt.
* Journal replay tooling for the ablation harness. The data model supports it; the harness
  itself is unwritten.
* Pagination / large-output handling on ingest. Command output is currently truncated only
  in the prompt (20 KB), never in the journal.
* Retention. `journal.jsonl` grows without bound; fine at CTF scale, unmeasured beyond it.

---

## 24. Open questions and ADR candidates

**ADR-1 — Promotion mints a new ID; the prefix always matches the current layer.**
*(decided)*
Promoting `A017` produces a new fact `F00N` with `promoted_from: "A017"`. The prefix
therefore never lies about which layer an item is in, and no client has to learn the rule
"read the level from the containing map, not the name". The cost is that a client holding
`A017` must follow the diff to find the successor — which is why `promoted` reports the
**old** ID, so a stale reference is still resolvable from the diff alone.

The consequence worth flagging: this makes ID reuse possible in a way it was not before,
because a promoted item vacates its number. Hence the persisted high-water marks in
`meta.fact_seq` / `meta.assumption_seq` (§7). Those also close a pre-existing hole where
`remove` freed an ID for reissue.

**ADR-2 — `mark_tried` as a seventh mutation operation.** *(decided)*
Required for `only_untried` to mean anything. Scoped to flipping a flag on a template-owned
ID. If Guessing ever needs richer per-challenge state (attempt notes, timestamps,
outcomes), that is a real ADR rather than an extension of this op.

**ADR-3 — Human notes as admissible evidence for a Fact.** *(decided, §11)*
Reconciles 設計規劃書 §6.2 with the 共通設計起點 §10 fixture. Observation vs inference is
prompt-enforced; human-vs-agent is code-enforced. If it turns out models over-promote human
notes in practice, the fallback is to require an explicit `observed: true` marker on the
note input — which would be a contract change and therefore needs all three of us.

**ADR-4 — No-op ingest does not increment the revision.** *(decided)*
Keeps "the board changed" meaningful. 設計規劃書 §20 says every commit increments the
revision; a no-op is not a commit.

**Open — CHAP citation (arXiv 2606.09751).** Carried over from the design documents and
not independently verifiable from this environment. Confirm before publication.

**Open — challenge summary regeneration.** `challenge_summary` is overwritten wholesale by
each proposal. Whether it should instead be edited incrementally, or regenerated only every
N revisions, is untested.

**Open — evidence for reclassified items.** Reclassification preserves `evidence`
untouched. If a template change should itself be citable on the items it moved, say so —
right now only the journal records it.

---

## 25. Implementation status

Complete and verified at the end of this task:

| area | status |
|---|---|
| Typed domain model (`models.py`) | done — all 15 frozen contract types |
| `spec/openapi.yaml`, `memory.schema.json`, `template.schema.json` | done — generated, drift-tested |
| `spec/protocol.md` | done — handwritten, includes the Definition-of-Done checklist |
| State store: load / init / validate / atomic save / revision check / per-challenge commit lock | done |
| Journal: append-only, stable IDs, evidence lookup | done |
| Template registry: load, validate, resolve, reclassify | done |
| Summarizer: protocol, epistemic policy gate, `FakeSummarizer`, AIS3 provider, bounded repair | done |
| Service: create / ingest / read / query / guess / mutate / reclassify | done |
| Semantic diff | done |
| HTTP API: 7 endpoints + `/healthz` + list | done |
| Mock server (`--fake-summarizer`) for Person 2/3 | done |
| Tests | 163 passing offline, 2 opt-in live (verified against the real backend), zero network |
| `ruff check`, `ruff format --check`, `mypy --strict memory_core` | all clean |

Against 設計規劃書 §20 (MVP acceptance):

- [x] create a challenge
- [x] store a human note
- [x] store a normalized agent run log
- [x] journal preserves the raw input
- [x] Summarizer produces a validated proposal
- [x] Fact/Assumption obey the epistemic policy
- [x] Guessing comes only from templates
- [x] each commit increments the revision (a no-op is not a commit — ADR-4)
- [x] stale mutation returns 409
- [x] atomic state write
- [x] ingest and human mutation do not lost-update
- [x] every update returns a semantic diff
- [x] explicit template reclassification
- [x] query answers reference memory IDs
- [x] guess supports `seed`
- [x] core tests need no real AIS3 API; live test is opt-in
- [x] Person 2 can build the CLI from the OpenAPI alone
- [x] Person 3 can build the bridge and templates from the OpenAPI + template schema alone
- [x] Person 1 did not need either of the other two to finish integration tests

Not started, by design: Human CLI/TUI, Agent Bridge, real CTF technique catalogs, YOLO
loop, ablation harness.
