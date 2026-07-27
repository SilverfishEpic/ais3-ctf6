# Template Catalogs

`templates/` contains the curated CTF category catalogs that drive deterministic Guessing.
Memory Core owns the loader, schema validation, deterministic selection, challenge-local
tried state, and reclassification semantics. Template content changes should be reviewed as
contract-affecting data changes because clients render template ids and Guessing labels.

## Current Catalogs

- `generic`
- `web`
- `pwn`
- `rev`
- `crypto`

Every catalog must validate against `spec/template.schema.json`. Guess IDs are stable
catalog identifiers; neither Agent Bridge nor the model may invent additional Guess IDs at
runtime.

## Ownership Rules

- Memory Core owns the loader, schema, deterministic selection, tried state, and
  reclassification semantics.
- Human Client and Web UI use `GET /v1/templates` for template picker metadata and Guessing
  labels; they do not read `templates/*.yaml` directly.
- Agent Bridge may use template catalogs as read-only Guessing context through Memory Core.
- Future `forensics` and `misc` catalogs remain roadmap items until implemented and tested.

## Review Checklist

- each template has a stable `id` and `version`;
- section names match schema constraints;
- guessing techniques have stable IDs, titles, and tags;
- no secrets, private challenge data, or environment-specific paths are committed.
