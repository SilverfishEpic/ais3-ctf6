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
- `human_client/` - Human CLI package; exposes the `ctfmem` console command.
- `spec/` - normative protocol, OpenAPI, and JSON schemas.
- `templates/` - minimal Memory Core fixture templates only.
- `scripts/memctl.py` - developer/admin helper for exercising Memory Core.
- `docs/` - maintained documentation index, architecture notes, contracts, and roadmap.

Agent Bridge implementations belong on their own branches. Memory Core and Human CLI must
not contain Claude orchestration, YOLO loops, or full CTF technique catalogs.

## Human CLI quick start

`ctfmem` is the human-facing HTTP client for Memory Core. The most reliable invocation is
through `uv run`:

```bash
uv run ctfmem --help
```

If you install the package with `uv pip install -e .`, the executable is installed inside
the project virtual environment. Use `source .venv/bin/activate` on Linux/macOS or
`.\.venv\Scripts\Activate.ps1` on Windows PowerShell before calling `ctfmem` directly.

Full installation and Linux/Windows usage notes are in
[`docs/human-cli.md`](docs/human-cli.md).

## Local Verification

Use Python 3.12.

```bash
uv run --python 3.12 --extra dev pytest
uv run --python 3.12 --extra dev ruff check .
uv run --python 3.12 --extra dev mypy memory_core
uv run --python 3.12 --extra dev python scripts/gen_spec.py --check
```

Live AIS3 checks are opt-in and must keep credentials only in the Memory Core process
environment.
