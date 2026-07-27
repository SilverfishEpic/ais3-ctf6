# Architecture

The v2 system is a shared CTF memory service used by humans and external agents.
Memory Core is not an autonomous agent framework. The only model-driven component inside
Memory Core is the Summarizer.

## Ownership

Memory Core owns:

- challenge state schema and append-only source journal;
- Fact, Assumption, and per-challenge Guessing state;
- template loading and explicit reclassification;
- Summarizer interface, AIS3 provider, and epistemic policy enforcement;
- semantic diff, query, deterministic guessing, revision rules, and persistence;
- the canonical HTTP/domain contract under `spec/`.

Human Client owns:

- HTTP API client behavior and CLI UX;
- human notes, stdin input, command capture, display, query, guess, mutation, template
  commands, and JSON output;
- the local single-user web UI, which is the same client behavior behind a page rather than
  a terminal (see `adr/0001-human-web-ui.md`);
- future optional TUI work.

Agent Bridge owns:

- vendor-specific Agent adapters;
- provider-neutral run-log normalization;
- read-only memory skill;
- normal and YOLO orchestration;
- same-session/new-session behavior, cancellation, and run-log ingestion;
- real category templates and curated Guessing catalogs.

## Boundaries

Clients must not write `state.yaml` or `journal.jsonl`, allocate authoritative Fact or
Assumption IDs, mutate Guessing catalogs locally, call the Summarizer directly, or keep a
second semantic-memory representation.

All semantic state changes go through the Memory Core HTTP API.

## Current Implementation

Implemented on `main`:

- Memory Core service, persistence, revision checks, semantic diffs, fake Summarizer, AIS3
  provider, generated OpenAPI/JSON schemas, and curated template catalogs;
- Human CLI (`ctfmem`) for challenge management, ingest, read/query/guess, mutation,
  template changes, command capture, and agent run/status/stop;
- local Human Web UI (`ctfmem-web`) as a same-origin, loopback-only Human Client surface;
- Agent Bridge normal and YOLO execution, same-session/new-session behavior, memory skill,
  cancellation, status files, and sanitized offline replay fixtures.

Still planned or explicitly optional:

- optional TUI/cockpit work beyond the current web UI;
- explicit terminal-session recorder after separate design review;
- richer per-turn Agent Bridge runtime telemetry;
- persistence scaling only after measurement shows YAML is insufficient.
