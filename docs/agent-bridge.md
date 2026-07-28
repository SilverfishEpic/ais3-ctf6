# Agent Bridge

Agent Bridge connects external agent execution to the shared Memory Core.
It is a client of the public HTTP contract, not a second Memory Core.

## Responsibilities

- normalize provider-native messages and tool hooks into provider-neutral run-log events;
- submit completed run logs through `POST /v1/challenges/{id}/ingest`;
- inject a read-only memory snapshot or diff at turn start according to experiment policy;
- expose read-only memory tools (`memory_show`, `memory_query`, `memory_guess`) when enabled;
- orchestrate normal one-turn runs and YOLO multi-turn runs;
- own same-session/new-session behavior, cancellation, replay fixtures, and template catalogs.

Agent Bridge does not write `state.yaml`, append `journal.jsonl`, assign Fact/Assumption IDs,
promote Assumptions, edit Guessing state, or call the Memory Core Summarizer directly.

## Event Outcome Semantics

Every projected `tool_result` carries `outcome` from the canonical `AgentRunEvent`
contract:

| Outcome | Meaning | Fact-grade evidence |
| --- | --- | --- |
| `succeeded` | tool executed and completed successfully | yes |
| `failed` | tool executed and returned an error/non-zero result | yes |
| `denied` | policy blocked execution before the tool ran | no |
| `interrupted` | execution did not complete | no |
| `unknown` | provenance is insufficient | no |

Legacy tool results without `outcome` are treated by Memory Core as `unknown`.

## Provider Model

The bridge's stable runtime seam is `AgentAdapter`. `AgentLoop` depends on that protocol
and must remain unaware of provider SDKs, subprocess protocols, CLI event formats, or MCP
configuration details.

Current production support is Claude plus replay fixtures. Codex has an MVP adapter through
the documented non-interactive CLI JSONL stream. The provider registry keeps those paths out
of `AgentLoop`.

CLI shape:

```bash
ctfmem agent run --provider claude "..."
ctfmem agent run --provider codex --session new --no-memory-skill "..."
ctfmem agent run --provider fake --fake-agent multi_tool "..."
```

The default remains Claude. Omitting `--provider` while passing `--fake-agent` selects the
replay adapter for backward compatibility. Provider-specific event objects are normalized
before ingest; Memory Core only receives the shared `IngestEnvelope` shape.

Codex MVP constraints:

- it uses `codex exec --json`;
- it supports only `--session new`;
- it requires `--no-memory-skill`;
- it maps unverifiable provider output to `outcome=unknown`;
- live Codex execution is opt-in by operator action and is not part of the default test
  suite.

## Sessions and Looping

`AgentLoop` owns the experiment session mode and validates it against adapters that expose
`session_kind`. Invalid combinations fail at construction time instead of silently mixing
new-session and same-session behavior.

In same-session YOLO mode the first turn receives the full board. Later turns receive the
previous Agent-ingest diff when the revision is exactly the revision produced by that
ingest. If a human mutation advances the Memory Core revision between turns, the bridge
injects a full current snapshot on the next turn because there is no diff-history endpoint
for reconstructing the external change.

Stop requests are explicit through `StopSignal`. During YOLO execution the loop watches the
stop signal while the adapter turn is running. If the signal is raised, it calls
`adapter.cancel()`, awaits the interrupted run result when the adapter can provide it, and
still ingests completed partial events.

## Memory Skill

The Agent-facing memory skill is read-only. It may show the current memory, answer grounded
queries, and request deterministic guessing candidates from Memory Core. It must not expose
mutation, YAML editing, local ID allocation, or template rewriting.

The long-term target is a provider-neutral MCP memory skill. Claude currently receives the
skill through the Claude SDK path. Codex should receive it only after the MCP setup, secret
boundary, and read-only behavior are verified.

## Offline Replay

`fixtures/claude/*.jsonl` are sanitized deterministic replay fixtures for:

- `talk_only`
- `single_tool`
- `multi_tool`
- `tool_failure`
- `sandbox_denied`
- `interrupted`

Default tests use these fixtures and do not require Claude credentials or network access.
Future Codex fixtures must follow the same rule: sanitized replay data first, live provider
checks opt-in only. Live Claude, Codex, and AIS3 checks must never be required by the
default offline suite.
