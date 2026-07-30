# Human Web UI (`ctfmem-web`)

A local, single-user page for reading and editing the whiteboard. It is the web equivalent
of `ctfmem` and does exactly what `ctfmem` does: it talks to Memory Core over the frozen
HTTP contract and writes nothing itself.

The web UI exists by explicit ADR exception rather than by historical proposal drift. The
reasoning and accepted boundaries are in [`adr/0001-human-web-ui.md`](adr/0001-human-web-ui.md).
Read that first if you are reviewing this component.

## Running it

Memory Core first, then the page. They are separate processes and the page does not start
or supervise the core.

```bash
python -m memory_core --fake-summarizer --runs ./runs --templates ./templates --port 8777
```

```bash
python -m human_client.web --api-url http://127.0.0.1:8777 --port 8787
```

Then open `http://127.0.0.1:8787`. Installed as a package it is also `ctfmem-web`.

| Flag | Default | Meaning |
|---|---|---|
| `--port` | `8787` | Port to serve the page on. |
| `--api-url` | `CTFMEM_API_URL`, else `http://127.0.0.1:8000` | Memory Core base URL. |
| `--actor` | `CTFMEM_ACTOR`, else `$USER` | Attributed to every write. |
| `--timeout` | `CTFMEM_TIMEOUT`, else `30` | Read timeout for the routes that only touch local persistence. |
| `--core-timeout` | `330` | Read timeout for ingest, query, and creation with an initial input. |
| `--agent-workspace` | saved workspace, else unset | Directory an agent run works in. This explicit flag overrides the workspace saved by `ctfmem /agent init <path>`. |
| `--agent-runtime-dir` | `.ctfmem` | Where run status and stop files live. |
| `--agent-max-turns` | `10` | Ceiling. The page may ask for fewer, never more. |
| `--agent-max-cost-usd` | `5.0` | Ceiling, same rule. |
| `--agent-modes` | `normal,yolo` | Comma-separated `ctfmem agent run --mode` values the page may offer. |
| `--agent-sessions` | `same,new` | Comma-separated `ctfmem agent run --session` values the page may offer. |
| `--agent-memory-policies` | `full,facts,facts-assumptions,none` | Comma-separated memory policies the page may offer. |
| `--agent-fake-agents` | all recorded scenarios | Comma-separated replay scenarios the page may offer; pass an empty value to hide them. |

There is no `--host`. The server binds `127.0.0.1` by construction because it is
unauthenticated and can write.

`--core-timeout` must be at least Memory Core's own Summarizer read budget multiplied by
the number of times an ingest may re-summarize after a concurrent commit. Both of those are
Memory Core's to change, so the flag states the rule rather than hard-coding the arithmetic;
the resolved value is printed in the boot banner.

Template metadata comes from Memory Core's `GET /v1/templates` endpoint. The page never
reads `templates/*.yaml`; Memory Core remains the only template loader and validator.

## What is on the page

**Whiteboard.** Fact and Assumption, split by section. The section list comes from the
whiteboard response: Memory Core materializes every section the challenge's template
declares, so what you see is the real layout and not just whatever happens to be populated.
Clicking an item id copies it into the Override form.

**Observe** submits a note or a command result. It has no section control, because on this
path the Summarizer decides where the result lands. The result panel tells you where it
landed.

**Override** applies one controlled mutation: add, replace, remove, promote, demote, move.
You pick the section. `replace` has no section control because `ReplaceOperation` has no
such field. Every operation requires a reason, and the reason is journalled with it.

**Reclassify** replaces the template. Ids and epistemic levels survive; only sections are
remapped, and any Guessing technique whose id does not exist in the new catalog loses its
tried state.

**Guessing** is the whole catalog as a checklist. Technique ids and tried state come from
the challenge; titles and tags come from Memory Core's template API. The separate draw at
the bottom is `POST /guess`: with a fixed seed it is reproducible, which is what ablation
runs need.

The spinner at the top of that panel draws one technique and offers to record it. Two things
about it are deliberate:

- **The reel is decoration; the server decides.** The technique that comes up is whatever
  `POST /guess` returned, so a fixed seed still draws the same one twice. Rolling the dice in
  JavaScript would have made the seed a lie.
- **It records a hypothesis, not a technique name.** `spec/protocol.md` §2 permits
  `Guessing technique -> concrete challenge hypothesis -> Assumption`, and the middle step is
  the point: an Assumption is supposed to be challenge-specific. So the draw pre-fills an
  editable sentence rather than writing `SQL Injection` onto the board, where it would tell a
  teammate nothing. Provenance is kept in the tags — `guessing` plus the technique id — and
  the reason is journalled with the mutation like any other override.

Drawing a technique does not mark it tried, because you have not tried it. That flag stays
where it belongs, on the checklist, after you actually run something.

**Ask** queries the current whiteboard.

**Agent** starts `ctfmem agent run`. It appears only when an agent workspace was either saved
with `ctfmem /agent init <path>` or passed as `--agent-workspace` at launch; otherwise the
routes answer 403 and the panel is not drawn.

## The agent panel

The page does not orchestrate anything. It assembles arguments and launches the CLI as a
child process, then reads the same status and stop files the CLI uses. So a run started in
the browser is visible to `ctfmem agent status` in a terminal and can be stopped with
`ctfmem agent stop`, and a run started in a terminal shows up in the page. There is one
implementation of an agent run and this executes it. `docs/adr/0002-web-agent-runs.md`
records why it must work this way rather than driving the loop inside the server.

What the browser may choose: prompt, mode, session, memory policy, fake-agent replay
scenario, and the two limits. Those choices are **CLI-equivalent**: each one maps to an
argument already accepted by `ctfmem agent run`. The local server decides which values are
available with `--agent-modes`, `--agent-sessions`, `--agent-memory-policies`, and
`--agent-fake-agents`; the page can only choose from those allowlists.

What it may not choose: the workspace, runtime directory, actor, API URL, or any limit
above the launch ceiling. A larger turn/cost request is silently clamped down, and the
ceilings are printed next to the controls. The sandbox is the CLI path's default
`ToolPolicy`: read-only tools plus a recon command allowlist, with shell operators, command
substitution and wrapper executables denied before the tool runs. Widening it means
changing the CLI/Agent Bridge capability, not sending a wider browser request.

Starting a run shows the exact argument list that was launched. For something that spawns a
process on your machine, being able to read what ran matters more than a tidy banner.

**There is no separate progress bar, because the whiteboard is one.** Every turn that finds
something ingests it and moves the revision, and the page polls faster — two seconds instead
of six — while a run is live. What the status line adds at the end is turns, productive
turns, cost and why it stopped. Per-turn telemetry does not exist: the status file is written
at start and at end only.

Stopping writes the stop file. The loop checks it between turns, so it finishes the turn it
is on and then stops; whatever it already found is on the board. Stopping the web server
does *not* stop a running agent — the stop file is the way, from either front end.

The `agent` picker selects a recorded scenario instead of the real model. Those replays come
from real Claude runs, cost nothing and need no key, which makes them the right way to
demonstrate the feature or check a change. It defaults to the real model.

## Concurrency

Every write carries `expected_revision`. Where that value comes from differs by control, on
purpose:

- The **Override** and **Reclassify** forms capture the revision when you start filling them
  in, not when you submit. If the whiteboard moves while you are composing, submission is
  disabled and you are offered a recompose button. Capturing at submit time instead would
  silently swallow whatever changed while you were typing.
- The **Guessing checklist** uses the revision on screen at the moment you click. A checkbox
  has no composition window, so there is nothing to go stale.

A 409 is never retried automatically. The page re-reads the board, tells you which revision
you composed against and which one the core is at, and stops. `spec/protocol.md` §5 says the
client re-reads and retries; `docs/human-cli.md` narrows that to a human-initiated retry, and
the recompose button is that retry.

Polling is suspended while a form is armed or a write is in flight, so the revision cannot
change underneath an open form.

Note that 409 is shared: `challenge_exists` is also 409. Only `revision_conflict` produces
the conflict banner.

## Errors

Memory Core's errors arrive unmodified — original status, original
`{"error": {code, message, details}}` — and are shown with the code and the details intact.

Errors this server originates use a different envelope, `{"webui_error": {...}}`, so you can
always tell who answered. It reports `core_unreachable` as 503 rather than 502, because 502
is spent on `summarizer_unavailable`, which means the opposite: the input reached the core
and was journalled before the Summarizer failed.

The page renders four body shapes: the core's error envelope, the proxy's, FastAPI's
validation list, and FastAPI's plain `detail` string.

## Boundaries

- The page orchestrates nothing. It launches `ctfmem agent run` and reads the files that
  run writes; it never builds an adapter or drives a loop.
- The page holds no authority: no ids, no diffs, no second copy of memory. Refreshing loses
  nothing that mattered.
- `actor` comes from server configuration, never from the page.
- The proxy validates no request body. A missing reason or a malformed operation is answered
  by Memory Core in its own words.
- Template labels and picker choices come from Memory Core's `GET /v1/templates` endpoint.
  The Web UI does not read `templates/*.yaml` directly.

## Verification

Offline, in WSL:

```bash
uv run --python 3.12 --extra dev pytest tests/human_client
```

`tests/human_client/test_web_proxy.py` checks what the proxy forwards against a recording
client, and derives its expected route table from `spec/openapi.yaml` so it tracks the
contract rather than an implementation. `tests/human_client/test_web_endtoend.py` runs the
same paths against a real Memory Core with the deterministic Summarizer.

There are no JavaScript tests. The page is therefore verified by hand, and the pass has to
cover every operation, not the easy ones: create, list, note, command result, all six
mutations, mark tried, reclassify, draw, and ask, plus a real revision conflict, an
exhausted catalog, and Memory Core being down.

`tests/human_client/test_web_agent.py` covers argument assembly against an injected spawn,
and separately launches the real entry point to prove it runs. It cannot cover the spawn
end to end: a child reaches Memory Core over a real socket, and the suite's network guard
forbids that. So the last stretch is a hand pass with `--fake-agent`, which costs nothing:

```bash
python -m human_client.web --api-url http://127.0.0.1:8777 --agent-workspace ./runs
```

Worth checking specifically, because each one has failed at least once:

- a `multi_tool` run moves the revision and puts something on the board;
- a `sandbox_denied` run produces **no Fact** — a denied tool result is not evidence, and
  "rm is not in the allowlist" must never become something the board asserts;
- a `yolo` run stops at the launch ceiling rather than the CLI default;
- after a run completes the run button is enabled again, not stuck;
- `ctfmem agent status --runtime-dir <same dir>` in a terminal describes the run the browser
  started. That one is the whole architecture in a single command.
