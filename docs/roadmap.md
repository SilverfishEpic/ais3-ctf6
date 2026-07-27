# Roadmap

This roadmap separates implemented Memory Core behavior from future client and research
work. Future phases are not complete until their exit criteria are met by tests and
documentation.

## Current Status

Memory Core, Human CLI, and Agent Bridge are integrated on `main`. The current
implementation provides the v2 HTTP contract, persistence, fake Summarizer, AIS3
provider, revision safety, semantic diffs, query, deterministic Guessing, curated
template catalogs, `ctfmem`, `ctfmem agent`, and the local `ctfmem-web` cockpit.

Remaining work is follow-up hardening and feature expansion: richer Agent Bridge
runtime telemetry, optional TUI or deeper cockpit features, explicit terminal-session
ingestion after design review, broader evaluation/ablation, and persistence scaling only
if measurement requires it.

## Phase 0 - Contract And Core Hardening

Owner: Memory Core. Historical branch: `memory-system`.

Entry criteria: current `main` baseline and agreed v2 architecture.

Exit criteria:

- canonical schemas and OpenAPI generated from typed models;
- outcome-aware direct evidence for agent tool results;
- revision safety and no silent lost updates;
- consolidated docs;
- offline core tests pass.

Non-goals: Human CLI, Claude orchestration, real category catalogs.

## Phase 1 - Stable Human CLI

Owner: Human Client. Historical branch: `mixin`.

Exit criteria:

- HTTP-only `ctfmem`;
- notes and stdin;
- streaming command capture;
- show/query/guess/mutation/template commands;
- JSON mode and clear 409 conflicts.

Non-goals: TUI, terminal recorder, Summarizer access.

## Phase 2 - Agent Bridge Normal Mode

Owner: Agent Bridge. Historical branch: `redesign/agent-bridge-v2`.

Exit criteria:

- normalized Claude run log with execution outcomes;
- read-only memory skill wired by default when enabled;
- one-turn execution;
- ingest and diff handling;
- contract tests against Memory Core.

## Phase 3 - YOLO And Session Ablation

Owner: Agent Bridge.

Exit criteria:

- same-session and new-session modes;
- cancellation and partial-run ingestion;
- human mutation visible on the next loop;
- loop limits and telemetry.

## Phase 4 - Template Coverage

Owner: Agent Bridge/template work stream.

Exit criteria:

- curated `web`, `pwn`, `rev`, and `crypto` catalogs;
- later `forensics` and `misc` only after implementation and tests;
- stable Guessing IDs and template quality review.

## Phase 5 - Human Cockpit

Owner: Human Client.

Exit criteria:

- local `ctfmem-web` surface for whiteboard read/write, query, Guessing, template changes,
  and revision-safe intervention;
- same-origin proxy that forwards Memory Core responses without redefining the contract;
- optional `ctfmem agent run` façade governed by ADR 0002;
- future optional TUI or richer timeline views only when they add value beyond the web UI.

Non-goal: domain logic inside the web UI or any future TUI.

## Phase 6 - Terminal-Session Ingestion

Owner: Human Client, after explicit design review.

Exit criteria:

- explicit PTY/session recorder;
- stdin/stdout/stderr segments;
- batching instead of per-line summarization;
- privacy and redaction controls;
- no background summarization without user-visible design approval.

## Phase 7 - Evaluation And Ablation

Owner: cross-branch research.

Milestones:

- no memory vs Fact only vs Fact+Assumption vs full;
- generic vs category template;
- Guessing on/off with fixed seed;
- same/new session;
- human intervention on/off;
- reproducible journal replay.

## Phase 8 - Scale Only If Evidence Requires It

Owner: Memory Core, only after measurement.

Exit criteria:

- YAML limits measured and documented;
- alternative persistence justified by data.

Non-goals: database/vector store by default.

## Risks

- branch-local contract forks;
- accidentally treating provider-denied tool results as evidence;
- committing secrets or raw private recordings;
- client UX growing a second memory system;
- YOLO loops losing partial run logs or ignoring human intervention.
