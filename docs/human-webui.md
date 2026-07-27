# Human Web UI (`ctfmem-web`)

A local, single-user page for reading and editing the whiteboard. It is the web equivalent
of `ctfmem` and does exactly what `ctfmem` does: it talks to Memory Core over the frozen
HTTP contract and writes nothing itself.

`web GUI` appears on the enforced non-goal list in `docs/memory-core-v2.md`. The reasoning
for building one anyway, and the boundaries it accepts in exchange, are in
[`adr/0001-human-web-ui.md`](adr/0001-human-web-ui.md). Read that first if you are
reviewing this component.

## Running it

Memory Core first, then the page. They are separate processes and the page does not start
or supervise the core.

```bash
python -m memory_core --fake-summarizer --runs ./runs --templates ./templates --port 8777
```

```bash
python -m human_client.web --api-url http://127.0.0.1:8777 --templates ./templates --port 8787
```

Then open `http://127.0.0.1:8787`. Installed as a package it is also `ctfmem-web`.

| Flag | Default | Meaning |
|---|---|---|
| `--port` | `8787` | Port to serve the page on. |
| `--api-url` | `CTFMEM_API_URL`, else `http://127.0.0.1:8000` | Memory Core base URL. |
| `--actor` | `CTFMEM_ACTOR`, else `$USER` | Attributed to every write. |
| `--templates` | required | Where to read technique titles and the template list. |
| `--timeout` | `CTFMEM_TIMEOUT`, else `30` | Read timeout for the routes that only touch local persistence. |
| `--core-timeout` | `330` | Read timeout for ingest, query, and creation with an initial input. |

There is no `--host`. The server binds `127.0.0.1` by construction because it is
unauthenticated and can write.

`--core-timeout` must be at least Memory Core's own Summarizer read budget multiplied by
the number of times an ingest may re-summarize after a concurrent commit. Both of those are
Memory Core's to change, so the flag states the rule rather than hard-coding the arithmetic;
the resolved value is printed in the boot banner.

Start it with `--templates` pointing at the same directory Memory Core was started with. If
they differ, the page says so where it matters rather than silently showing you a catalog
the core does not have.

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
the challenge; titles and tags come from the template files. The separate draw at the bottom
is `POST /guess`: with a fixed seed it is reproducible, which is what ablation runs need.

**Ask** queries the current whiteboard.

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

- No agent orchestration. `ctfmem agent run/stop` is not exposed and will not be.
- The page holds no authority: no ids, no diffs, no second copy of memory. Refreshing loses
  nothing that mattered.
- `actor` comes from server configuration, never from the page.
- The proxy validates no request body. A missing reason or a malformed operation is answered
  by Memory Core in its own words.
- `human_client/web/catalog.py` is the only part that touches the filesystem, it is
  read-only, and it is used for labels alone.

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
