# Contracts

The normative contract is:

1. `spec/protocol.md`
2. `spec/openapi.yaml`
3. `spec/memory.schema.json`
4. `spec/template.schema.json`
5. canonical Pydantic models in `memory_core/models.py`, from which generated specs are
   checked.

## Ingest

Humans and agents both submit an `IngestEnvelope` to:

```text
POST /v1/challenges/{challenge_id}/ingest
```

Human inputs use `note` or `command_result`. Agent Bridge inputs use `run_log` with
provider-neutral `AgentRunEvent` rows.

## Evidence Outcome

Agent `tool_result` events carry an optional `outcome`:

- `succeeded` - tool executed and completed successfully;
- `failed` - tool executed and returned an error/non-zero result;
- `denied` - tool did not execute;
- `interrupted` - tool did not complete;
- `unknown` - execution provenance is insufficient.

Only `succeeded` and `failed` are direct evidence. Missing legacy outcomes are treated as
`unknown`.

## Client Rules

Clients may read, query, guess, ingest, mutate through explicit override, and request
template changes through HTTP. They may not edit YAML files, maintain their own
authoritative revision state, invent memory IDs, or call the Summarizer.

## Drift Check

Run:

```bash
uv run --python 3.12 --extra dev python scripts/gen_spec.py --check
```
