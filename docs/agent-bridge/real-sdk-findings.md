# Agent Bridge Real SDK Findings

This file is the maintained, sanitized summary of the Claude SDK observations used by the
offline fixtures. It intentionally excludes private transcripts, credentials, and
environment-specific paths.

## Message Shapes

- `AssistantMessage` may contain `TextBlock`, `ThinkingBlock`, and `ToolUseBlock`.
- `UserMessage` may carry `ToolResultBlock` entries that correspond to tool calls.
- `ResultMessage` carries session and terminal metadata, including permission denials and
  interrupted terminal reasons.
- `SystemMessage` and rate-limit/progress events are telemetry and are skipped from memory
  evidence events.

## Tool Provenance

`PostToolUse` and `PostToolUseFailure` hooks prove that a tool actually executed. A stream
`ToolResultBlock` without matching hook provenance is treated conservatively:

- permission-denial metadata maps to `outcome: denied`;
- known interruption markers map to `outcome: interrupted`;
- otherwise the bridge emits `outcome: unknown`.

Only `succeeded` and `failed` can be direct evidence under the Memory Core contract.

## Fixture Policy

Replay fixtures are **real recordings of Claude, redacted in place** — not synthesized
stand-ins. `tools/record_run.py` writes them; `tools/redact_fixtures.py` then replaces
machine-specific absolute paths with `<workspace>` / `<home>` / `<project>` and leaves
everything else byte-identical.

Everything else stays: real `usage` and `cache_*` token counts, real `costUSD`, real
`duration_ms`, real tool stdout/stderr, real model ids. Those are the point of the
fixtures — they are what a synthesized file cannot supply.

An audit of the recordings found no credentials and no provider authorization headers
(the SDK never puts them in the message stream), and `ThinkingBlock` carries a signature
with an empty `thinking` string, so there is no chain-of-thought to strip. The only
sensitive content was the absolute paths, which is exactly what the redactor removes.

Two things depend on the recordings being real rather than minimal:

- **Truncation.** `truncate.py` budgets 60,000 chars per run with a 2,000/1,000 head/tail
  per event. A hand-written fixture is ~1 KB and never reaches any threshold.
- **Summarizer prompt tuning.** Real tool output is messy — permission bits, tracebacks,
  trailing whitespace. A Summarizer tuned against idealized input drifts the moment it
  meets a real run log. 設計規劃書 §2.3 and `docs/agent-bridge/m0-findings.md` both call
  this out.
