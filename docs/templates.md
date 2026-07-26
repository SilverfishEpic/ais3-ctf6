# Agent Bridge Template Catalogs

This branch owns the real curated CTF category catalogs that drive deterministic Guessing.
Memory Core only requires minimal fixtures to prove registry, classification,
reclassification, and Guessing mechanics.

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

- Template content changes belong on the Agent Bridge branch.
- Memory Core owns the loader, schema, deterministic selection, tried state, and
  reclassification semantics.
- Human Client does not own template content.
- Future `forensics` and `misc` catalogs remain roadmap items until implemented and tested.

## Review Checklist

- each template has a stable `id` and `version`;
- section names match schema constraints;
- guessing techniques have stable IDs, titles, and tags;
- no secrets, private challenge data, or environment-specific paths are committed.
