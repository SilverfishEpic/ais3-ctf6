# ADR 0003 - Agent Provider Abstraction for Claude and Codex

## Status

Proposed.

## Context

Agent Bridge was integrated with a deliberately narrow first production path:
Claude Agent SDK execution is normalized into the shared `AgentRunEvent` contract and
then submitted to Memory Core through the public HTTP API. That boundary is correct, but
the current implementation still exposes several Claude-first seams:

- `ctfmem agent run` builds `ClaudeAdapter` directly;
- `build_envelope()` defaults to `provider="anthropic"`;
- replay fixtures and documentation describe the bridge as Claude-oriented;
- sandbox enforcement is implemented as a Claude SDK `PreToolUse` hook;
- the read-only memory skill is wired through Claude SDK MCP helpers.

The next provider target is Codex. Codex must not become a second orchestration path, and
it must not gain private access to Memory Core state. The required system shape remains:

```text
Human
  ↓
ctfmem agent run
  ↓
Agent Bridge provider adapter
  ↓
Memory Core public HTTP API
```

Codex support is only acceptable if native Codex events are projected into the same
provider-neutral run-log contract used by Claude. Memory Core must continue to receive
`IngestEnvelope` objects, not Codex SDK objects, CLI transcripts, or provider-specific
domain models.

## Decision

Introduce an explicit provider abstraction behind `ctfmem agent run`.

The CLI should select an adapter through a registry instead of importing provider classes
directly:

```text
ctfmem agent run --provider claude "..."
ctfmem agent run --provider codex "..."
ctfmem agent run --provider fake --fake-agent multi_tool "..."
```

The default remains `claude` until Codex reaches the same acceptance gate. This preserves
current behavior and keeps the change incremental.

The provider registry owns construction. `AgentLoop` remains provider-neutral and should
not learn about Claude, Codex, subprocesses, SDK clients, or MCP configuration details.

```text
human_client.agent_cli
  ↓
agent_bridge.adapters.registry
  ├── ClaudeAdapter
  ├── CodexAdapter
  └── FakeAgentAdapter
  ↓
AgentLoop
  ↓
MemoryClient.ingest()
```

## Provider Identity

`source.provider` in Memory Core journal entries identifies the agent runtime provider,
not the underlying model vendor alone. Accepted values should be documented examples, not
closed enums unless the OpenAPI contract is deliberately changed.

Recommended values:

| Adapter | `source.provider` |
| --- | --- |
| Claude adapter | `anthropic` |
| Codex adapter | `openai-codex` |
| replay adapter | `fake` |

The model name remains `source.model` when the adapter can determine it. Provider session
metadata remains opaque and is stored only as `run_id` and `session_id`.

## Codex Adapter Strategy

The first Codex adapter should prefer the documented non-interactive CLI surface:

```text
codex exec --json
```

Reasons:

- it is available as an external process boundary like the current web `ctfmem agent run`
  delegation model;
- JSONL output gives a machine-readable stream of thread, turn, item, command execution,
  MCP tool, and error events;
- the integration can be tested with subprocess fixtures without live Codex credentials;
- it avoids adding Codex SDK dependencies before the event and session semantics are
  proven.

The initial MVP support level was:

- `mode=normal`;
- `session=new`;
- read-only memory board injection through the prompt;
- JSONL event normalization into `AgentRunEvent`;
- conservative `tool_result.outcome` mapping;
- interruption handling that still ingests completed partial events.

The implemented CLI bridge now also supports `session=same` by resuming the exact Codex
thread id reported by the first turn. This brings Codex loop/session behavior in line with
the Agent Bridge session model while keeping provider event normalization outside Memory
Core.

The implementation now supports same-session resume, read-only Codex MCP memory tools, and
Codex subprocess credential isolation. It still does not claim parity for
Claude-equivalent pre-tool command allowlist enforcement; that remains a separate
verification item.

## Event Normalization

Provider-native events must be normalized outside Memory Core.

Recommended module layout:

```text
agent_bridge/normalize.py          # shared dataclasses and wire projection helpers
agent_bridge/normalize_claude.py   # Claude rows -> NormalizedRun
agent_bridge/normalize_codex.py    # Codex JSONL rows -> NormalizedRun
```

If that split is too disruptive, keep `normalize.py` as the public import surface and move
provider-specific implementations behind private helpers.

Codex event mapping must be conservative:

| Codex event | AgentRunEvent |
| --- | --- |
| agent text item | `role=assistant`, `kind=message` |
| command execution started | `role=assistant`, `kind=tool_call`, `name="command_execution"` |
| command execution completed with exit 0 | `role=tool`, `kind=tool_result`, `outcome=succeeded` |
| command execution completed with non-zero exit | `role=tool`, `kind=tool_result`, `outcome=failed` |
| sandbox or approval denial | `role=tool`, `kind=tool_result`, `outcome=denied` |
| cancelled or interrupted turn | `role=tool`, `kind=tool_result`, `outcome=interrupted` |
| unverifiable transcript-only output | `outcome=unknown` |

The adapter must not treat Codex final prose as command evidence. Only executed command or
tool-result events with clear provenance may become Fact-grade evidence.

## Tool Policy and Sandbox

Claude currently enforces `ToolPolicy` with a provider hook before tool execution. Codex
must not be documented as equally protected until an equivalent enforcement point is
implemented and tested.

The abstraction should therefore split policy description from provider enforcement:

```text
ToolPolicy
  ↓
ProviderToolPolicyAdapter
  ├── Claude: SDK PreToolUse hook
  └── Codex: Codex sandbox / execpolicy / hook / external isolation layer
```

Codex currently runs with `--sandbox read-only`, `--ignore-user-config`, and
`--ignore-rules`. This prevents the bridge path from silently inheriting local Codex
configuration or project rules that widen authority. The adapter also removes Memory Core
provider environment variables before spawning Codex.

This is not the same mechanism as Claude's `PreToolUse` command allowlist. Any future
support for broader write or network access must be explicit, server-owned, and documented
as higher risk.

## Memory Skill and MCP

The read-only memory skill should become provider-neutral MCP capability:

```text
memory_show
memory_query
memory_guess
```

It must remain read-only. It must not expose mutation, template switching, YAML editing,
journal appending, local ID allocation, or Summarizer access.

Codex receives this surface through a scoped stdio MCP server implemented by
`agent_bridge.memory_mcp`. The registry enables it only when the selected memory policy
has `skill_enabled=True` and the operator did not pass `--no-memory-skill`. The process is
started with the Memory Core API URL, challenge id, policy name, and timeout; it does not
receive AIS3 provider credentials.

The bridge still injects the current board or diff as prompt context through `AgentLoop`.
This preserves provider-neutral behavior and gives the agent useful context even if the
provider declines to call tools.

## Web UI Consequences

ADR 0002 remains in force: the web UI starts agent runs by spawning `ctfmem agent run`.
The web layer must not import provider adapters or implement a separate Codex runner.

When multiple providers exist, the web UI may expose provider selection only through
server-owned allowlists:

```text
ctfmem-web --agent-providers claude,codex,fake
ctfmem-web --agent-default-provider claude
```

The browser can select from the allowlist, but cannot choose executables, workspaces,
credentials, sandbox modes, or extra tool authority.

## Phases

### Phase 1 - Provider Registry

- add `--provider`;
- create adapter registry;
- preserve existing Claude and fake behavior;
- make provider identity explicit in envelopes;
- update tests and documentation.

Exit criteria:

- existing Claude and fake tests pass unchanged in behavior;
- `ctfmem agent run --provider claude` is equivalent to today's default;
- unknown provider produces a stable usage error.

### Phase 2 - Codex CLI MVP

- implement `CodexAdapter` using `codex exec --json`;
- add sanitized Codex JSONL fixtures;
- normalize Codex events into `AgentRunEvent`;
- support normal and new-session runs.

Exit criteria:

- Codex fixtures validate as `IngestEnvelope`;
- successful, failed, denied, interrupted, and unknown outcomes are covered;
- offline suite does not require Codex credentials or network;
- a live Codex smoke test is opt-in.

### Phase 3 - Codex Memory MCP

- expose the read-only memory skill through a provider-neutral MCP server;
- wire Codex to that MCP server through scoped configuration;
- verify Codex never receives AIS3 credentials.

Exit criteria:

- Codex can call `memory_show`, `memory_query`, and `memory_guess`;
- Memory Core remains the only state writer;
- MCP failures degrade clearly instead of silently removing memory access.
- Codex and its MCP server do not receive AIS3 provider credentials.

### Phase 4 - Session and YOLO Parity

- prove Codex resume or Codex SDK same-session behavior;
- implement `continue_run()`;
- enable YOLO after cancellation and partial-event ingestion are verified.

Exit criteria:

- same-session and new-session behavior are distinguishable in tests;
- stop requests interrupt a running Codex turn;
- completed partial events are still ingested;
- no semantic mutation is retried without human confirmation.

## Acceptance Gate

Codex support is mergeable only when:

- provider selection is explicit and documented;
- Claude behavior remains compatible;
- Codex native events never enter Memory Core directly;
- every Codex tool result has a conservative outcome;
- unknown provenance is not treated as Fact-grade evidence;
- Codex does not receive AIS3 credentials;
- the default test suite stays offline;
- live Codex verification is opt-in;
- web-started runs still delegate to `ctfmem agent run`.

## Consequences

**Positive.**

- Agent Bridge becomes a real multi-provider boundary instead of a Claude-specific path.
- Memory Core remains stable because provider differences are normalized before ingest.
- Codex can be introduced incrementally without weakening existing Claude guarantees.

**Negative, accepted.**

- Codex MVP will not be feature-equivalent to Claude on day one.
- Sandbox and MCP parity require separate proof.
- More fixtures are needed because replay data must reflect real provider event shapes.

## Non-goals

- direct Codex writes to `state.yaml` or `journal.jsonl`;
- direct Codex calls to AIS3 or the Memory Core Summarizer;
- a second web-only Codex runner;
- automatic semantic mutation retries;
- treating free-form Codex prose as command evidence;
- enabling YOLO for Codex before same-session and cancellation semantics are verified.
