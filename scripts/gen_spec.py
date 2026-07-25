#!/usr/bin/env python3
"""Generate ``spec/`` from the canonical Pydantic models.

There is exactly one source of truth — ``memory_core/models.py`` — and everything under
``spec/`` is derived from it.  Hand-maintaining a second copy of the schema is how three
developers end up with three subtly different contracts, so ``tests/test_spec_drift.py``
fails the build if the committed files stop matching what this script produces.

Usage::

    python scripts/gen_spec.py           # regenerate
    python scripts/gen_spec.py --check   # exit 1 if the committed spec is stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402

from memory_core.api import create_app  # noqa: E402
from memory_core.journal import Journal  # noqa: E402
from memory_core.models import MemorySnapshot, TemplateDefinition  # noqa: E402
from memory_core.service import MemoryService  # noqa: E402
from memory_core.store import StateStore  # noqa: E402
from memory_core.summarizer import FakeSummarizer  # noqa: E402
from memory_core.templates import TemplateRegistry  # noqa: E402

SPEC_DIR = REPO_ROOT / "spec"


def build_openapi() -> dict[str, Any]:
    """Build the app without touching the filesystem state of a real runs directory."""

    service = MemoryService(
        store=StateStore(REPO_ROOT / "runs"),
        journal=Journal(REPO_ROOT / "runs"),
        templates=TemplateRegistry.from_dir(REPO_ROOT / "templates"),
        summarizer=FakeSummarizer(),
    )
    app = create_app(service)
    schema: dict[str, Any] = app.openapi()
    return schema


def render() -> dict[str, str]:
    """Return ``{relative filename: file content}`` for every generated spec artifact."""

    openapi = build_openapi()
    memory_schema = MemorySnapshot.model_json_schema(mode="serialization")
    memory_schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    memory_schema["title"] = "MemorySnapshot"
    memory_schema["description"] = (
        "Persisted whiteboard for one challenge: the contents of runs/<challenge-id>/state.yaml."
    )

    template_schema = TemplateDefinition.model_json_schema(mode="serialization")
    template_schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    template_schema["title"] = "TemplateDefinition"
    template_schema["description"] = (
        "A CTF category template: section layout plus a curated Guessing technique catalog."
    )

    header = (
        "# GENERATED FILE - do not edit by hand.\n"
        "# Source of truth: memory_core/models.py + memory_core/api.py\n"
        "# Regenerate with: python scripts/gen_spec.py\n"
    )
    return {
        "openapi.yaml": header
        + yaml.safe_dump(openapi, sort_keys=False, allow_unicode=True, width=100),
        "memory.schema.json": json.dumps(memory_schema, indent=2, ensure_ascii=False) + "\n",
        "template.schema.json": json.dumps(template_schema, indent=2, ensure_ascii=False) + "\n",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify without writing")
    args = parser.parse_args(argv)

    SPEC_DIR.mkdir(parents=True, exist_ok=True)
    stale: list[str] = []
    for name, content in render().items():
        path = SPEC_DIR / name
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current == content:
            continue
        if args.check:
            stale.append(name)
        else:
            path.write_text(content, encoding="utf-8")
            print(f"wrote {path.relative_to(REPO_ROOT)}")

    if args.check and stale:
        print("stale spec files: " + ", ".join(stale), file=sys.stderr)
        print("run: python scripts/gen_spec.py", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
