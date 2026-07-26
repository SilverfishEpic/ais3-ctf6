# Memory Core

Memory Core is the single writer and contract owner for Shared Memory v2.

## Responsibilities

- create and list challenges;
- ingest normalized human/agent inputs;
- journal inputs before summarization;
- enforce Fact/Assumption epistemic policy;
- serve deterministic memory reads, query answers, and Guessing candidates;
- apply revision-aware human mutations;
- reclassify templates explicitly;
- persist valid YAML state atomically.

`scripts/memctl.py` is a developer/admin utility for exercising this API. It is not the
Human Client product surface and must not grow into agent orchestration.

## Templates

The `memory-system` branch keeps only minimal fixture templates needed to verify registry,
classification, reclassification, and Guessing mechanics. Real curated CTF category
catalogs belong to the Agent Bridge/template work stream and are documented as future
integration work until reviewed.

## Verification

Use Python 3.12.

```bash
uv run --python 3.12 --extra dev pytest
uv run --python 3.12 --extra dev ruff check .
uv run --python 3.12 --extra dev mypy memory_core
uv run --python 3.12 --extra dev python scripts/gen_spec.py --check
```

Live AIS3 tests are opt-in with `AIS3_LIVE_TEST=1`.
