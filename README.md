# AIS3 CTF Shared Memory System v2

This repository contains the v2 shared-memory architecture for CTF workflows.

Memory Core is the authoritative service. Humans and external agents are clients that
talk to Memory Core through the frozen HTTP/domain contract under `spec/`.

## Branch Roles

```text
main
  └── memory-system          # Memory Core, canonical specs, common docs
        ├── mixin            # Human-facing HTTP CLI/client
        └── agent-bridge-v2  # Agent Bridge, Claude adapter, real template catalogs
```

Only `memory-system` is intended to merge directly into `main`. Client branches should be
reviewed against `memory-system`, not directly against `main`, until Memory Core is merged.

## Current Components

- `memory_core/` - canonical Memory Core implementation.
- `spec/` - normative protocol, OpenAPI, and JSON schemas.
- `templates/` - Agent Bridge-owned curated CTF category catalogs on this branch.
- `scripts/memctl.py` - developer/admin helper for exercising Memory Core.
- `docs/` - maintained documentation index, architecture notes, contracts, and roadmap.

Human Client implementations belong on their own branch. This branch adds Agent Bridge
code, Claude orchestration, replay fixtures, and real template catalogs on top of the
Memory Core baseline.

## Local Verification

Use Python 3.12.

```bash
uv run --python 3.12 --extra dev pytest
uv run --python 3.12 --extra dev ruff check .
uv run --python 3.12 --extra dev mypy memory_core agent_bridge
uv run --python 3.12 --extra dev python scripts/gen_spec.py --check
```

Live AIS3 checks are opt-in and must keep credentials only in the Memory Core process
environment.
