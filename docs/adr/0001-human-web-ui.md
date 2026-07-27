# ADR 0001 - A Local Human Web UI For Memory Core

## Status

Proposed. This ADR exists because `web GUI` is on an enforced non-goal list, and the rule
attached to that list requires an ADR before an implementation, not after one. Acceptance
requires agreement from all three developers, per `docs/memory-core-v2.md`.

Superseded in part by [ADR 0002](0002-web-agent-runs.md): the sentence below stating that
agent orchestration "is not exposed and will not be" no longer holds. Agent runs are now
exposed, under the guardrails 0002 records. The rest of this document stands.

## The Non-Goal This ADR Addresses

`docs/memory-core-v2.md` lists `web GUI` under a heading that reads "Non-goals (enforced,
not aspirational)", and states the agreed rule:

> write an ADR describing the observed problem first - do not implement it and document it
> afterwards.

`docs/CTF共享記憶系統_v2_設計規劃書.md` §21 repeats `web GUI` in its 明確非目標 list.

`spec/protocol.md` §14 does not list `web GUI`. That is silence, not a reversal:
`docs/README.md` demotes a historical document only where it *disagrees* with `spec/`, and
`spec/` says nothing here. The non-goal therefore stands until this ADR is accepted.

## Observed Problem

The whiteboard is a two-dimensional structure: two epistemic layers, each partitioned into
the sections its template declares, plus a per-challenge Guessing checklist of up to 39
techniques. `ctfmem` renders it one command at a time.

Four concrete costs follow, all observed while operating the current CLI:

1. **The layout is not visible.** A template declares its full section list and Memory Core
   materializes every declared section, but reading them means `ctfmem show` per layer and
   holding the result in your head. There is no view in which the whole board is on screen.
2. **Every override is a two-step with state carried by the operator.** A promotion requires
   reading the item ID and the current `revision`, then retyping both into
   `ctfmem mutate promote`. The `expected_revision` handshake that makes the system
   concurrency-safe is, at the CLI, a value the human transcribes.
3. **Guessing is a checklist rendered as a sample.** `ctfmem guess` draws N techniques.
   The state that matters during a challenge - which of the catalog's techniques are still
   untried - has no single view, and `mark-tried` is the only writer of that state.
4. **The round-trip cost pushes work off the whiteboard.** During a challenge the operator
   is already switching between a target, a shell, and notes. When recording an observation
   costs a context switch, observations stop being recorded, and the system's entire value
   proposition - that the whiteboard is where the truth lives - degrades to an empty file.

The Human Client is the component whose job is exactly this: `spec/protocol.md` §1 gives
Person 2 "diff rendering, JSON output" and CLI UX; `docs/roadmap.md` Phase 5 already
anticipates a "Human Cockpit" with a diff timeline and revision-safe intervention. The
disagreement between that roadmap entry and the non-goal list is that Phase 5 says TUI.
This ADR argues the same cockpit is better delivered in a browser, and that the reasons the
original non-goal list rejected a web GUI do not apply to what is proposed here.

## Why The Original Non-Goal Was Right, And What Changed

The non-goal list is a list of things that would have grown a second system. Read in
context, its neighbours are `WebSocket collaboration`, `multi-process writers`,
`distributed locking`, `RBAC`, `autonomous memory-graph evolution`. The concern is
coherent: a web GUI usually arrives with a server, a session store, an auth model, a push
channel, and a client-side cache that becomes a second source of truth.

What is proposed here has none of those. It is a static page and a proxy. It holds no
state, authenticates nobody, pushes nothing, and cannot write anything Memory Core did not
authorize through the frozen contract.

## Decision

Add `human_client/web/`: a local, single-user, same-origin server that serves one static
page and forwards `/api/*` to Memory Core through the existing `MemoryCoreClient`.

- **Same origin, because cross-origin does not work and should not be made to work.**
  `memory_core/api.py` mounts no CORS middleware. Adding one would mean editing the
  authoritative service to satisfy a client's deployment shape. Serving the page from the
  proxy's own origin makes the question disappear.
- **Scope: read plus human writes.** Challenge create/list, whiteboard read, `note` and
  `command_result` ingest, query, guess, the six mutation operations, `mark_tried`, and
  template reclassification. Every one of them is an operation `ctfmem` already exposes.
- **No agent orchestration.** `ctfmem agent run/stop` is deliberately excluded. A web
  endpoint that can start a subprocess on the operator's machine is a different decision
  with a different threat model, and it is not this one. *(Superseded by ADR 0002, which
  takes that decision explicitly and records the guardrails it comes with.)*
- **Zero new dependencies.** `fastapi`, `uvicorn`, `httpx`, `pyyaml`, and `pydantic` are
  already declared.
- **Loopback only, no auth.** `127.0.0.1` is bound by construction; there is no `--host`
  flag. A `Host` header check is included, because same-origin is a browser judgement keyed
  on hostname and DNS rebinding satisfies it. That is an origin control, not an auth model.

## What This Does Not Change

- Memory Core remains the only semantic-memory writer. The WebUI writes no YAML, allocates
  no IDs, computes no diffs, and calls no Summarizer.
- `spec/` and `memory_core/` are not modified by the implementing branch, and a diff check
  enforces it.
- The client keeps no second semantic-memory representation, per `docs/architecture.md`.
  Its in-page state is a render of the last response and is discarded on refresh.
- Memory Core errors reach the browser verbatim - original status, original
  `{"error": {code, message, details}}`. Errors the proxy itself originates use a
  structurally distinct envelope so they can never be mistaken for the core's, and avoid
  the status codes the frozen table in `spec/protocol.md` §10 already spends.

## Alternatives Considered

**Add CORS to `memory_core`, ship a purely static page.** Fewer moving parts, but it edits
the authoritative service to accommodate a client, and it puts a permissive origin policy
on the process that owns every write. Rejected.

**A separate React/Vite front end.** Conventional, but it introduces npm, a build step, and
a dependency tree into a repository that is deliberately Python-only with six runtime
dependencies. Rejected as disproportionate.

**The TUI in `docs/roadmap.md` Phase 5.** Legitimate and still open. It is rejected here
only on reach: the board is two-dimensional and wide, and a browser renders it without a
layout engine written by hand. If this ADR is rejected, Phase 5 remains the fallback.

## Consequences

**Positive.** The whole board is visible at once. The `expected_revision` handshake becomes
mechanical rather than transcribed, which removes the most likely source of a stale-write
mistake. The Guessing catalog becomes the checklist it always was. The distinction the
system is built on - the Summarizer decides where an ingested observation lands, the human
decides where an explicit mutation lands - becomes visible in the interface instead of
implicit in which subcommand was typed.

**Negative, and accepted.**

- `human_client/web/catalog.py` becomes a second reader of `templates/*.yaml`, files whose
  loader and schema `spec/protocol.md` §8 assigns to Person 1. It is read-only, never on a
  write path, never authoritative, and used only to label a template picker. Section lists
  come from the API, not from these files. This is the one boundary this ADR knowingly
  approaches, and it needs Person 1's explicit yes.
- The proxy's synchronous handlers occupy threadpool workers for the duration of a core
  call, and Memory Core's Summarizer budget is 300s. Read and write timeouts are separated
  and polling is suspended while a write is in flight, but a wedged core will still make the
  page feel wedged.
- One more surface to keep in step with the contract. Mitigated by forwarding responses
  unmodified, by sourcing the proxy's expected route table from `spec/openapi.yaml`, and by
  an end-to-end test that runs against a real `MemoryService`.

## Acceptance

This ADR is accepted when Person 1 and Person 3 agree to it. Two items need an explicit
answer rather than silence:

1. Person 1: may `human_client/web/catalog.py` read `templates/*.yaml` under the
   constraints above?
2. All three: does adding a browser client to the Human Client's surface change the
   ownership line in `docs/architecture.md`, or is it covered by "future optional TUI work"?
