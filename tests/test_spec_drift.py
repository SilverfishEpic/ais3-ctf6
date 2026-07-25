"""The contract in ``spec/`` must stay a faithful projection of the code.

Three developers working in parallel against a hand-maintained schema is how you end up
with three subtly different contracts.  ``memory_core/models.py`` is the single source of
truth, and this test fails the build the moment ``spec/`` drifts from it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_DIR = REPO_ROOT / "spec"
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import gen_spec  # noqa: E402


@pytest.mark.parametrize("name", ["openapi.yaml", "memory.schema.json", "template.schema.json"])
def test_committed_spec_matches_generated(name: str) -> None:
    expected = gen_spec.render()[name]
    actual = (SPEC_DIR / name).read_text(encoding="utf-8")
    assert actual == expected, f"spec/{name} is stale — regenerate with: python scripts/gen_spec.py"


def test_protocol_md_exists() -> None:
    """Handwritten on purpose: it carries the semantics a schema cannot express."""

    assert (SPEC_DIR / "protocol.md").is_file()


def test_openapi_declares_the_frozen_paths() -> None:
    document = yaml.safe_load((SPEC_DIR / "openapi.yaml").read_text(encoding="utf-8"))
    assert {
        "/v1/challenges",
        "/v1/challenges/{challenge_id}/ingest",
        "/v1/challenges/{challenge_id}/memory",
        "/v1/challenges/{challenge_id}/query",
        "/v1/challenges/{challenge_id}/guess",
        "/v1/challenges/{challenge_id}/mutate",
        "/v1/challenges/{challenge_id}/template",
    } <= set(document["paths"])


def test_openapi_declares_the_frozen_domain_types() -> None:
    document = yaml.safe_load((SPEC_DIR / "openapi.yaml").read_text(encoding="utf-8"))
    schemas = set(document["components"]["schemas"])
    frozen = {
        "IngestEnvelope",
        "InputSource",
        "AgentRunEvent",
        "MemoryReadResponse",
        "FactItem",
        "AssumptionItem",
        "GuessingState",
        "MemoryMutation",
        "MemoryDiff",
        "QueryRequest",
        "QueryResponse",
        "GuessRequest",
        "GuessResponse",
        "ErrorResponse",
    }
    assert frozen <= schemas, sorted(frozen - schemas)


def test_mutation_operations_are_a_closed_set() -> None:
    """No arbitrary patch operation may appear in the published contract."""

    document = yaml.safe_load((SPEC_DIR / "openapi.yaml").read_text(encoding="utf-8"))
    schemas = document["components"]["schemas"]
    ops = {
        name.replace("Operation", "").lower()
        for name in schemas
        if name.endswith("Operation") and name != "MutationOperation"
    }
    assert ops == {"add", "replace", "remove", "promote", "demote", "move", "marktried"}


def test_memory_schema_pins_schema_version_two() -> None:
    schema = json.loads((SPEC_DIR / "memory.schema.json").read_text(encoding="utf-8"))
    assert schema["properties"]["schema_version"]["const"] == 2
    assert schema["title"] == "MemorySnapshot"


def test_template_schema_requires_section_lists() -> None:
    schema = json.loads((SPEC_DIR / "template.schema.json").read_text(encoding="utf-8"))
    assert {"id", "version", "fact_sections", "assumption_sections"} <= set(schema["required"])


def test_shipped_templates_validate_against_the_published_schema() -> None:
    """The generated JSON Schema is checked against real files, not just itself."""

    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((SPEC_DIR / "template.schema.json").read_text(encoding="utf-8"))
    for path in sorted((REPO_ROOT / "templates").glob("*.yaml")):
        jsonschema.validate(yaml.safe_load(path.read_text(encoding="utf-8")), schema)
