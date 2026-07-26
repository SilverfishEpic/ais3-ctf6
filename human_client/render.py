from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from typing import Any, TextIO


def emit_json(payload: Any, *, out: TextIO | None = None) -> None:
    out = out or sys.stdout
    print(json.dumps(payload, ensure_ascii=False, indent=2), file=out)


def render_diff(payload: Mapping[str, Any], *, out: TextIO | None = None) -> None:
    out = out or sys.stdout
    before = payload.get("revision_before")
    after = payload.get("revision_after")
    print(f"Memory rev {before} → {after}", file=out)
    diff = _mapping(payload.get("diff"))
    sections = [
        ("FACT", "fact_added", "+"),
        ("FACT", "fact_updated", "~"),
        ("FACT", "fact_removed", "-"),
        ("ASSUMPTION", "assumption_added", "+"),
        ("ASSUMPTION", "assumption_updated", "~"),
        ("ASSUMPTION", "assumption_removed", "-"),
        ("PROMOTED", "promoted", "↑"),
        ("DEMOTED", "demoted", "↓"),
    ]
    printed_heading: set[str] = set()
    for heading, key, marker in sections:
        values = diff.get(key) or []
        if not values:
            continue
        if heading not in printed_heading:
            print(f"\n{heading}", file=out)
            printed_heading.add(heading)
        for value in values:
            print(f" {marker} {value}", file=out)
    template_changed = diff.get("template_changed")
    if isinstance(template_changed, Mapping):
        print("\nTEMPLATE", file=out)
        print(f" {template_changed.get('from')} → {template_changed.get('to')}", file=out)


def render_memory(payload: Mapping[str, Any], *, out: TextIO | None = None) -> None:
    out = out or sys.stdout
    challenge = _mapping(payload.get("challenge"))
    print(f"Challenge: {challenge.get('id', '?')}", file=out)
    print(
        f"Template: {challenge.get('template', 'generic')}@{challenge.get('template_version', 1)}",
        file=out,
    )
    print(f"Revision: {payload.get('revision')}", file=out)
    summary = challenge.get("summary")
    if summary:
        print(f"Summary: {summary}", file=out)
    _render_items("FACT", _mapping(payload.get("fact")), out=out)
    _render_items("ASSUMPTION", _mapping(payload.get("assumption")), out=out)
    guessing = payload.get("guessing")
    if isinstance(guessing, Mapping):
        print("\nGUESSING", file=out)
        print(f"Catalog: {guessing.get('catalog')}", file=out)
        state = _mapping(guessing.get("state"))
        for technique, local in state.items():
            tried = _mapping(local).get("tried", False)
            status = "tried" if tried else "untried"
            print(f"{technique}  {status}", file=out)


def render_query(payload: Mapping[str, Any], *, out: TextIO | None = None) -> None:
    out = out or sys.stdout
    print("Answer:", file=out)
    print(payload.get("answer", ""), file=out)
    references = payload.get("references") or []
    print("\nReferences:", file=out)
    for reference in references:
        print(str(reference), file=out)
    print(f"\nRevision: {payload.get('revision')}", file=out)


def render_guess(payload: Mapping[str, Any], *, out: TextIO | None = None) -> None:
    out = out or sys.stdout
    print(f"Catalog: {payload.get('catalog')} rev {payload.get('revision')}", file=out)
    for candidate in payload.get("candidates") or []:
        if isinstance(candidate, Mapping):
            print(f"\n{candidate.get('id')}", file=out)
            print(f"  {candidate.get('title')}", file=out)
            tags = candidate.get("tags") or []
            print(f"  tags: {', '.join(str(tag) for tag in tags)}", file=out)
            print(f"  {'tried' if candidate.get('tried') else 'untried'}", file=out)


def render_error(error: BaseException, *, out: TextIO | None = None) -> None:
    out = out or sys.stderr
    print(str(error), file=out)


def _render_items(title: str, sections: Mapping[str, Any], *, out: TextIO) -> None:
    print(f"\n{title}", file=out)
    for section, items in sections.items():
        print(f"\n[{section}]", file=out)
        for item in items if isinstance(items, list) else []:
            if isinstance(item, Mapping):
                print(f"{item.get('id')}  {item.get('text')}", file=out)
                evidence = item.get("evidence") or []
                if evidence:
                    print(f"      evidence: {', '.join(str(e) for e in evidence)}", file=out)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
