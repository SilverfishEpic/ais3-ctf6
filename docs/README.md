# Documentation Index

The normative contract lives in `spec/`. Prose documents explain intent and operating
boundaries, but they do not override the generated schemas or OpenAPI.

## Maintained Common Docs

- [Architecture](architecture.md) - component ownership and non-goals.
- [Memory Core](memory-core.md) - service responsibilities and verification.
- [Human CLI (`ctfmem`)](human-cli.md) - installation, Linux/Windows usage, command contract, and acceptance criteria.
- [Human Web UI (`ctfmem-web`)](human-webui.md) - the local page, its concurrency rules, and its boundaries.
- [Contracts](contracts.md) - how clients should use the shared API.
- [Branch Strategy](branch-strategy.md) - dependency and merge policy.
- [Agent Bridge](agent-bridge.md) - agent provider adapters, loop behavior, memory skill boundary.
- [Templates](templates.md) - category catalog ownership and validation.

## Decisions

- [ADR 0001 - A local human web UI](adr/0001-human-web-ui.md) - why a component on the
  non-goal list was built anyway, and the boundaries it accepts. Accepted.
- [ADR 0002 - Agent runs from the web UI](adr/0002-web-agent-runs.md) - supersedes 0001 on
  agent orchestration; why the web spawns `ctfmem agent run` rather than driving the loop
  itself, and the guardrails that come with it. Accepted.
- [ADR 0003 - Agent provider abstraction for Claude and Codex](adr/0003-agent-provider-abstraction.md)
  - provider boundary for Claude/Codex/Fake without changing Memory Core or the web
  orchestration model. Accepted.

Historical design drafts, one-off experiment reports, and SDK discovery notes are not kept
in `main`. They should be recovered from archived branches or tags when audit history is
needed. Current `main` documentation should describe the implemented system and active
decision records only.
