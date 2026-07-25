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

Replay fixtures are minimal and sanitized. They preserve SDK shape and outcome semantics
without carrying real credentials, private command paths, raw chain-of-thought, or provider
authorization headers.
