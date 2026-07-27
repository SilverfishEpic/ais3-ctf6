# ADR 0002 - Agent Runs From The Web UI

## Status

Accepted. Supersedes one line of [ADR 0001](0001-human-web-ui.md), which said agent
orchestration "is not exposed and will not be". Everything else in 0001 stands.

## Context

ADR 0001 drew the web UI's scope at read plus human writes and excluded `ctfmem agent`
explicitly, on the grounds that an HTTP endpoint able to start Claude on the operator's
machine is a different decision with a different threat model. That reasoning was right;
what it was not is permanent. The decision is now taken deliberately rather than by
omission.

The operator's case is straightforward: the CLI can drive Claude against a challenge, the
web UI is where they are already watching the whiteboard, and switching to a terminal to
start a run and back to the browser to watch its effects is the friction the web UI existed
to remove. The stated requirement alongside it was that the CLI and the web must not drift.

## Decision

The web UI starts an agent run by **spawning `ctfmem agent run` as a child process**. It
does not import the loop, does not build an adapter, and does not orchestrate anything
itself.

That is not a stylistic preference. Running the loop inside the web server is wrong for
four mechanical reasons:

1. `AgentLoop.run_yolo` calls `StopSignal.install()`, which is `signal.signal(SIGINT, ...)`.
   On the server's main thread that takes Ctrl-C away from uvicorn for the duration of a
   run; on a worker thread it raises `ValueError` and is silently suppressed, so the
   handler never installs.
2. `agent-status.json` records `os.getpid()`. If the web server is the runner, that pid is
   the server's, `process_is_running()` answers true forever, and `ctfmem agent status`
   reports a finished run as still running.
3. `agent_bridge/runtime.py` describes itself as client-owned control metadata and is
   already built as a cross-process seam: a status file and a stop file under
   `.ctfmem/<challenge>/`. Using it as intended means `ctfmem agent status` and
   `ctfmem agent stop` operate on a web-started run, and the web sees a terminal-started
   one.
4. A run takes minutes. No HTTP request can wait for it, so the work has to be detached
   regardless.

The drift requirement is then satisfied structurally rather than by discipline: there is
one implementation of an agent run, and the web executes it verbatim. `human_client/web/`
contributes argument assembly and nothing else. This is **equivalence by delegation**:
the browser may select the same categories of choices the CLI exposes, but only from the
allowlists the local server operator enabled at launch.

## Guardrails

The threat model that justified 0001's exclusion has not gone away, so the capability is
constrained rather than merely enabled.

- **Off by default.** Without `--agent-workspace` at launch the routes answer 403 and the
  page does not render the panel. Enabling it is an explicit act by whoever starts the
  server.
- **The workspace comes from the server.** The browser cannot choose which directory an
  agent runs against; anything it sends is ignored.
- **Limits clamp downward only.** `max_turns` and `max_cost_usd` are `min(requested,
  ceiling)`, where the ceiling is a launch flag. A page can make a run smaller, never
  larger, and the ceiling is shown to the operator.
- **CLI-equivalent choices, server-owned allowlists.** The page may offer `mode`,
  `session`, memory exposure policy, and fake-agent replay scenario choices. These are not
  invented by the web layer; they map to `ctfmem agent run` arguments. The specific values
  available in the browser are controlled by `ctfmem-web --agent-modes`,
  `--agent-sessions`, `--agent-memory-policies`, and `--agent-fake-agents`.
- **One run per challenge.** A start request is refused with 409 while a live pid is
  recorded for that challenge.
- **The sandbox is not reachable from the browser.** Runs use the default `ToolPolicy`:
  read-only tools plus a recon command allowlist, with shell operators, command
  substitution, and wrapper executables such as `sudo`, `env`, and `bash` denied by a
  `PreToolUse` hook before the tool executes. Widening it requires editing server code.
- **A cost-free path exists and is one click away.** `--fake-agent` replays recorded real
  Claude runs, so demonstrating and testing the feature costs nothing and needs no key.

What this does not defend against is unchanged from 0001: the server is unauthenticated on
loopback and relies on a `Host` check plus browser preflight rules. It also does not defend
against a widened `ToolPolicy` — `agent_bridge/sandbox.py` says plainly that an allowlist
containing an interpreter degrades from a sandbox to a guard rail, and that real challenges
need an isolation layer decided separately. If that widening happens, it applies to the web
route too, and this ADR should be revisited at that point rather than after.

## Consequences

**Positive.** The whiteboard becomes the progress display for free: every productive turn
ingests and moves the revision, and the page already polls, so the operator watches Facts
and Assumptions appear while the agent works. Control is shared with the terminal in both
directions. No second orchestration path exists to fall out of step.

**Negative, and accepted.**

- The spawn boundary cannot be covered by the offline suite. A child process reaches Memory
  Core over a real socket, which the suite's network guard forbids, so the argument
  assembly is tested against an injected spawn and the process itself is verified by hand
  with `--fake-agent`. This is stated in the docs rather than papered over.
- Per-turn telemetry does not exist. The status file is written at start and at end, so
  cost and turn counts arrive only when the run finishes. Emitting per-turn events would
  mean changing `human_client/agent_cli.py` or `agent_bridge/loop.py`, which is a separate
  change to modules this one does not otherwise touch.
- Stopping the web server does not stop a running agent. The stop file is the way to stop
  one, from either front end.

## Acceptance

Accepted under these implementation constraints:

1. Web-started runs delegate to `ctfmem agent run`; the web layer must not import or
   reimplement the agent loop.
2. The browser may choose CLI-equivalent run options only from server-owned allowlists.
3. The workspace, runtime directory, and authority ceilings are fixed by the local server
   process, not by request body fields.
4. Browser requests cannot widen sandbox or tool authority beyond what the CLI path and
   server launch flags already allow.
