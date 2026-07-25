# Memory Core v2 — Shared Protocol (contract v0)

This is the document the three of us agree on **before** writing independent code.
`openapi.yaml`, `memory.schema.json` and `template.schema.json` in this directory are
generated from `memory_core/models.py`; this file states the semantics that a schema
cannot express.

> **Memory Core is the only semantic-memory writer. The Human UI and every External Agent
> are API clients.**

As long as that sentence holds, the CLI, the TUI, Claude, YOLO loops, templates and
research experiments can all evolve independently.

---

## 1. Ownership

| Component | Owner | Owns | Must not |
|---|---|---|---|
| Memory Core | Person 1 | state, journal, templates loader, Summarizer, diff, revision semantics, HTTP API | agent orchestration, CLI rendering, real CTF catalogs |
| Human Client (`cli/`, `tui/`) | Person 2 | note / capture / show / query / guess / modify / template set, diff rendering, JSON output | writing `state.yaml` directly |
| Agent Bridge (`agent_bridge/`), templates | Person 3 | Claude adapter, event normalization, memory skill, normal/YOLO loops, session modes, real technique catalogs | writing `state.yaml` directly, sending provider-specific objects into Memory Core |

---

## 2. Three memory concepts, and only three

| Concept | Definition | Typical source |
|---|---|---|
| **Fact** | Atomic, challenge-specific, confirmed by direct evidence or an explicit human override. | tool output, `checksec`, file contents, HTTP behaviour, reproducible exploit, human observation |
| **Assumption** | Atomic, challenge-specific, plausible and testable, not yet sufficiently verified. | agent inference, human hypothesis, attack path inferred from several observations |
| **Guessing** | A curated technique catalog supplied by the challenge's template. **Not** free-form LLM brainstorming. | `templates/*.yaml` |

Permitted evolution:

```
Guessing technique -> concrete challenge hypothesis -> Assumption -> direct evidence -> Fact
```

Forbidden:

```
Guessing -> "the model thinks it sounds right" -> Fact
```

The append-only journal is a **source/evidence record**, not a fourth layer.

---

## 3. Epistemic policy (normative)

Enforced in `memory_core/summarizer.enforce_epistemic_policy` and covered by
`tests/test_summarizer_policy.py`.

* **R1** — every Fact cites at least one journal source ID that exists.
* **R2** — a Fact requires **direct evidence**:
  * `command_result` → direct
  * `run_log` containing at least one `tool_result` event → direct
  * a **human** `note` → direct (a first-party observer)
  * an **agent** `note`, or a `run_log` with narrative only → **not** direct
* **R3** — insufficient evidence means Assumption, or nothing. A Fact proposal failing
  R1/R2 is **downgraded to an Assumption**, not rejected outright and not accepted.
* **R4** — Assumption → Fact promotion by the model requires *new* direct evidence the
  item did not already carry. A human override without new evidence is legitimate but
  must go through `POST /mutate` with an actor and a reason.
* **R5** — the Summarizer can neither touch the Guessing catalog nor delete a Fact.
  Both are structurally impossible: `SummaryProposal` has no such fields. A Fact the
  model now doubts can only be *demoted*, which is visible in the diff.

R2's human-note carve-out is the one judgment call. A human note may contain an
observation ("I ran nmap and port 80 is open") or an inference ("this is probably
SQLi"). Only the observation may become a Fact. That distinction is instructed in the
prompt because no gate can decide it mechanically; what the gate *does* guarantee is the
asymmetry the design rests on — **a first-party human report is admissible evidence, an
external agent's narrative never is.** An agent must show a tool result.

---

## 4. Ingest envelope

One vendor-neutral contract for humans and agents alike.

```json
{
  "source": {
    "type": "human | agent",
    "actor": "ctshen",
    "provider": "anthropic",
    "model": "claude-...",
    "run_id": "run-123",
    "session_id": "session-abc"
  },
  "input": { "type": "note | command_result | run_log", "...": "..." }
}
```

* **note** — `{"type": "note", "content": "..."}`
* **command_result** — `{"type": "command_result", "command": ["nmap","-sV","target"], "exit_code": 0, "stdout": "...", "stderr": ""}`
* **run_log** — `{"type": "run_log", "events": [{"seq": 1, "role": "assistant", "kind": "message|tool_call|tool_result", "name": "Bash", "content": "..."}]}`

Claude SDK classes and other provider event objects are **never** part of the Memory Core
domain model. Person 3 owns `Claude events -> AgentRunEvent -> IngestEnvelope`; Person 2
owns `human text / process stdout+stderr -> IngestEnvelope`; Person 1 only ever sees the
normalized envelope.

Memory Core writes the envelope to `journal.jsonl` **before** the Summarizer runs, so a
summarization failure can never lose what actually happened.

---

## 5. Revision and concurrency

Every challenge carries `revision: <int>`, starting at 0.

* Every explicit mutation and reclassification sends `{"expected_revision": N}`.
* A stale value returns **409 Conflict** with `code: "revision_conflict"` and the current
  revision in `error.details`. The client re-reads and retries.
* Clients never edit YAML. Memory Core is the only writer.
* Commit is a **short critical section** per challenge; state is written via temp file +
  fsync + atomic replace.
* **No lock is held during an LLM call.** Ingest reads a snapshot at revision R, releases,
  summarizes, then re-acquires to commit; if the revision moved it re-reads and
  re-summarizes, bounded to 2 retries before returning 409.
* Atomic rename alone does **not** prevent a logical lost update — two writers both based
  on revision 41 each produce a valid file. The revision check inside the critical
  section is what prevents it. Both mechanisms are required.
* MVP promises a **single Memory Core process**. No multi-worker, no distributed lock, no
  network-filesystem multi-writer.

A no-op ingest (the Summarizer found nothing worth storing) does **not** burn a revision:
the response returns `revision_before == revision_after` with an empty diff.

---

## 6. Semantic diff

Every successful state-changing operation — ingest, mutate, template — returns:

```json
{
  "revision_before": 16,
  "revision_after": 17,
  "summary": "The new scan confirmed an HTTP service.",
  "diff": {
    "fact_added": ["F014"],
    "fact_updated": [],
    "fact_removed": [],
    "assumption_added": ["A009"],
    "assumption_updated": [],
    "assumption_removed": [],
    "promoted": [],
    "demoted": [],
    "template_changed": null
  }
}
```

`template_changed` is `null` or `{"from": "generic@1", "to": "web@1"}`.

Person 1 produces the diff. Person 2 only renders it. Person 3 may use it as loop
telemetry but never modifies it. UI formatting is not part of the core.

---

## 7. Memory identity

* IDs look like `F001` / `A001` and are assigned **by Memory Core**, never by a client and
  never by the model.
* The `F`/`A` prefix **always matches the layer the item currently lives in.**
* An ID is stable for as long as the item stays in its layer: a section move, an
  evidence/text update and template reclassification all preserve it.
* **Crossing layers mints a new ID.** Promoting `A017` produces a new fact `F00N` carrying
  `promoted_from: "A017"`; demoting is the mirror image. Follow `promoted_from` to
  reconstruct an item's epistemic history.
* **An ID is never reused**, even after its item is removed or promoted away. `A001` names
  exactly one claim for the whole life of the challenge, so every evidence reference and
  journal record minted under it still resolves. The counters are persisted in
  `meta.fact_seq` / `meta.assumption_seq`.
* In the diff, a promotion is reported under the **old** ID in `promoted`, while the new ID
  appears in `fact_added`. The old ID is deliberately **not** listed in
  `assumption_removed`: the claim did not go away, it changed epistemic status.

---

## 8. Template contract

```yaml
id: web
version: 1
description: Web security challenge
classifier_hints: [HTTP, cookie, API, login]
fact_sections: [target, endpoints, ..., general]
assumption_sections: [attack_surface, ..., general]
guessing:
  - id: WEB-SQLI
    title: SQL Injection
    tags: [database, input]
```

Invariants:

* `fact_sections` must contain `general`; `assumption_sections` must contain `general`.
* Guessing technique IDs are unique and stable within a template version.
* `state.yaml` stores only `guessing.catalog` (`"web@1"`) plus per-technique local state
  (`tried`, optional `note`). It never copies the catalog.
* Person 1 owns the loader, the schema and the reclassification algorithm.
  Person 3 owns the real technique content.

### Classification and reclassification

* A challenge may be classified **once**, at creation, from `initial_input`.
* Normal ingest never auto-reclassifies.
* Replacing a template is an explicit client operation: `POST /template`.
* Reclassification **must**: preserve every Fact/Assumption ID; preserve every epistemic
  level; remap only sections, by exact name, falling back to `general`; replace the
  Guessing catalog; carry `tried` state for technique IDs that still exist; return a
  semantic diff.

> Changing the category or the layout is not the same as re-judging what is true.

---

## 9. Mutation

`POST /v1/challenges/{id}/mutate`

```json
{
  "expected_revision": 42,
  "actor": "ctshen",
  "reason": "Verified manually in GDB",
  "operations": [{"op": "promote", "id": "A017", "to": "fact"}]
}
```

`reason` is mandatory. Controlled operations only:

| op | effect |
|---|---|
| `add` | create a Fact or Assumption in a section |
| `replace` | change an item's text / evidence / tags |
| `remove` | delete an item |
| `promote` | Assumption → Fact; new fact ID, `promoted_from` links back |
| `demote` | Fact → Assumption; new assumption ID, `promoted_from` links back |
| `move` | change section only; epistemic level untouched |
| `mark_tried` | flip a Guessing technique's local `tried` flag |

There is **no arbitrary YAML patch endpoint**. `mark_tried` is an addition beyond the
original six: `state.yaml` stores per-challenge `tried` flags and `only_untried` is
meaningless without a sanctioned way to set them. It is still controlled — it can flip a
flag on a template-owned identifier and nothing else.

Every mutation is journalled with actor, reason and operations, **inside** the commit
critical section, so a 409 never leaves a phantom override in the evidence log.

---

## 10. Endpoints

```
POST /v1/challenges
GET  /v1/challenges
POST /v1/challenges/{id}/ingest
GET  /v1/challenges/{id}/memory?layer=fact|assumption|guessing&section=<name>
POST /v1/challenges/{id}/query
POST /v1/challenges/{id}/guess
POST /v1/challenges/{id}/mutate
POST /v1/challenges/{id}/template
GET  /healthz
```

### Error model

```json
{"error": {"code": "revision_conflict", "message": "...", "details": {"current_revision": 43}}}
```

| code | status |
|---|---|
| `invalid_request` | 400 |
| `challenge_not_found`, `template_not_found` | 404 |
| `challenge_exists`, `revision_conflict` | 409 |
| `proposal_invalid` | 422 |
| `summarizer_unavailable` | 502 | backend down or too slow — **retryable**, the input is already journalled |
| `summarizer_unauthorized` | 500 | the server's AIS3 key was rejected — **not** retryable, an operator must fix it |
| `state_corrupt`, `invalid_template`, `internal_error` | 500 |

FastAPI request-body validation failures return its standard 422.

### Guess

```json
{"count": 3, "only_untried": true, "seed": 1337}
```

Deterministic given a seed — the same seed always draws the same techniques, which is what
makes ablation runs reproducible. The LLM is never used to invent techniques.

### Query

Answers come only from current whiteboard state. `references` is intersected with the live
snapshot before returning, so an invented ID cannot reach a client.

---

## 11. Mock server (unblocks Person 2 and Person 3 today)

```bash
python -m memory_core --fake-summarizer --runs ./runs --templates ./templates
```

`FakeSummarizer` is deterministic, needs no API key and makes no network call, and it obeys
the same epistemic policy as the real backend — so nobody builds against looser behaviour
than production.

---

## 12. First integration fixture

The agreed end-to-end scenario, implemented offline in `tests/test_e2e_demo_web.py`:

1. create challenge `demo-web`
2. human ingests `"I ran nmap and port 80 is open."`
3. the Summarizer produces `F001: TCP/80 is open.` and
   `A001: The target may expose an HTTP application.`
4. revision 0 → 1
5. `ctfmem show` can display F001 / A001
6. a fake agent queries memory
7. the fake agent returns a normalized run log
8. ingest → revision 1 → 2
9. human `promote` / `demote` / `replace` using `expected_revision`
10. a stale revision returns 409
11. `guess --seed 42` always draws the same techniques

---

## 13. Definition of Done — contract v0

- [x] Fact / Assumption / Guessing semantics agreed
- [x] `IngestEnvelope` frozen
- [x] `AgentRunEvent` frozen
- [x] `MemorySnapshot` frozen
- [x] Mutation operations frozen
- [x] Diff schema frozen
- [x] Template schema frozen
- [x] revision / 409 semantics frozen
- [x] OpenAPI endpoint paths frozen
- [x] Claude-specific data confined to the adapter layer
- [x] Human client does not write YAML
- [x] Agent client does not write YAML
- [x] only templates define the Guessing catalog
- [x] the Summarizer does no agent orchestration

Optional fields may be added later. Existing field semantics may not change without all
three of us agreeing.

---

## 14. Shared "do not add" list

Not in v0, for anybody's convenience: SQLite, Redis, vector DB, embeddings, knowledge
graph, event-sourcing framework, message broker, multi-agent coordinator, arbitrary YAML
patch endpoint, CLI writing state directly, agents writing state directly, realtime
collaborative editing, multi-worker Memory Core, PTY recorder, background daemon.

If one becomes genuinely necessary, write an ADR describing the observed problem it
solves — do not implement it first.
