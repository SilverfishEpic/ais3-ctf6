# Branch Strategy

Historical component branches (`memory-system`, `mixin`, `redesign/agent-bridge-v2`, and
related integration branches) have been squash-integrated and archived. Current work should
branch from the latest `main`.

## Current Policy

1. Start feature branches from current `main`.
2. Keep the public contract authoritative:
   - update `memory_core/models.py` / `memory_core/api.py` first when changing API shape;
   - regenerate `spec/openapi.yaml`;
   - keep `spec/protocol.md` aligned with the semantic contract.
3. Keep component boundaries intact:
   - Memory Core is the only semantic-memory writer and template loader;
   - Human Client and Web UI use Memory Core only through HTTP/OpenAPI;
   - Agent Bridge normalizes provider events and uses the public API.
4. Do not add branch-local forks of `spec/`, `memory_core/`, templates, or client models.
5. Do not commit runtime state, credentials, private transcripts, or local run artifacts.
6. Prefer squash merge for feature PRs unless a reviewer explicitly needs preserved
   internal history.

## Review Gates

Before merge, run the relevant subset plus the full offline suite when the change crosses
component boundaries:

```bash
uv run --extra dev pytest
uv run --extra dev ruff check memory_core human_client agent_bridge scripts tools tests
uv run --extra dev mypy memory_core human_client agent_bridge
uv run --extra dev python scripts/gen_spec.py --check
```

For packaged/CLI-facing changes, also run:

```bash
uv build
```

Live AIS3 or Claude checks remain opt-in and must not require committing credentials.
