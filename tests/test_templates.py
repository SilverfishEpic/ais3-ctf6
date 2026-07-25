"""Template loading and reclassification.

The governing rule: changing the category changes the *layout*, never the *truth*.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from tests.conftest import human_note

from memory.core.errors import InvalidTemplate, TemplateNotFound
from memory.core.models import (
    AssumptionItem,
    ChallengeMeta,
    CreateChallengeRequest,
    FactItem,
    GuessingState,
    GuessingTechniqueState,
    IngestEnvelope,
    MemoryMutation,
    MemorySnapshot,
    TemplateChangeRequest,
    TemplateDefinition,
)
from memory.core.service import MemoryService
from memory.core.templates import TemplateRegistry, load_template, reclassify, validate_template

MINIMAL = {
    "id": "tiny",
    "version": 1,
    "fact_sections": ["general"],
    "assumption_sections": ["general"],
    "guessing": [],
}


def _write(tmp_path: Path, name: str, data: dict[str, object]) -> Path:
    path = tmp_path / f"{name}.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_valid_template_loads(tmp_path: Path) -> None:
    template = load_template(_write(tmp_path, "tiny", MINIMAL))
    assert template.catalog == "tiny@1"


def test_template_missing_fact_general_is_rejected(tmp_path: Path) -> None:
    bad = {**MINIMAL, "fact_sections": ["target"]}
    with pytest.raises(InvalidTemplate, match="fact.general"):
        load_template(_write(tmp_path, "bad", bad))


def test_template_missing_assumption_general_is_rejected(tmp_path: Path) -> None:
    bad = {**MINIMAL, "assumption_sections": ["paths"]}
    with pytest.raises(InvalidTemplate, match="assumption.general"):
        load_template(_write(tmp_path, "bad", bad))


def test_duplicate_guessing_ids_are_rejected() -> None:
    template = TemplateDefinition.model_validate(
        {
            **MINIMAL,
            "guessing": [
                {"id": "X-ONE", "title": "one"},
                {"id": "X-ONE", "title": "two"},
            ],
        }
    )
    with pytest.raises(InvalidTemplate, match="duplicate guessing"):
        validate_template(template)


def test_registry_requires_a_generic_fallback(tmp_path: Path) -> None:
    _write(tmp_path, "tiny", MINIMAL)
    with pytest.raises(InvalidTemplate, match="generic"):
        TemplateRegistry.from_dir(tmp_path)


def test_unknown_template_raises(registry: TemplateRegistry) -> None:
    with pytest.raises(TemplateNotFound):
        registry.get("does-not-exist")


def test_shipped_templates_are_valid(registry: TemplateRegistry) -> None:
    assert registry.ids() == ["generic", "web"]


# -- reclassification --------------------------------------------------------


def _snapshot() -> MemorySnapshot:
    return MemorySnapshot(
        revision=7,
        challenge=ChallengeMeta(id="demo", title="Demo", template="generic", template_version=1),
        fact={
            "target": [FactItem(id="F001", text="TCP/80 is open.", evidence=["I-1"])],
            "artifacts": [FactItem(id="F002", text="chall.zip contains an ELF.")],
        },
        assumption={
            "next_checks": [AssumptionItem(id="A001", text="try the login form")],
            "attack_paths": [AssumptionItem(id="A002", text="maybe auth confusion")],
        },
        guessing=GuessingState(
            catalog="generic@1", state={"GEN-ENUM": GuessingTechniqueState(tried=True)}
        ),
    )


def test_reclassification_preserves_ids_and_epistemic_level(registry: TemplateRegistry) -> None:
    result = reclassify(_snapshot(), registry.get("web"))

    fact_ids = {item.id for _, item in result.iter_facts()}
    assumption_ids = {item.id for _, item in result.iter_assumptions()}
    assert fact_ids == {"F001", "F002"}
    assert assumption_ids == {"A001", "A002"}
    # A Fact stays a Fact. Re-laying-out the board is not re-judging it.
    assert "F001" not in assumption_ids
    assert "A001" not in fact_ids


def test_unmappable_sections_fall_back_to_general(registry: TemplateRegistry) -> None:
    """`artifacts` exists in generic but not in web, so F002 must land in general."""

    result = reclassify(_snapshot(), registry.get("web"))
    assert [i.id for i in result.fact["target"]] == ["F001"]  # section exists in both
    assert [i.id for i in result.fact["general"]] == ["F002"]  # section does not
    assert [i.id for i in result.assumption["next_checks"]] == ["A001"]
    assert [i.id for i in result.assumption["attack_paths"]] == ["A002"]


def test_reclassification_preserves_evidence(registry: TemplateRegistry) -> None:
    result = reclassify(_snapshot(), registry.get("web"))
    assert result.fact["target"][0].evidence == ["I-1"]


def test_reclassification_replaces_the_guessing_catalog(registry: TemplateRegistry) -> None:
    result = reclassify(_snapshot(), registry.get("web"))
    assert result.guessing.catalog == "web@1"
    assert set(result.guessing.state) == {t.id for t in registry.get("web").guessing}
    assert "GEN-ENUM" not in result.guessing.state  # no longer in the catalog


def test_reclassification_carries_tried_state_for_surviving_techniques(
    registry: TemplateRegistry,
) -> None:
    snapshot = _snapshot()
    snapshot.guessing = GuessingState(
        catalog="web@1", state={"WEB-SQLI": GuessingTechniqueState(tried=True)}
    )
    snapshot.challenge.template = "web"
    result = reclassify(snapshot, registry.get("web"))
    assert result.guessing.state["WEB-SQLI"].tried is True
    assert result.guessing.state["WEB-SSRF"].tried is False


def test_set_template_via_service_returns_a_diff(service: MemoryService) -> None:
    service.create_challenge(CreateChallengeRequest(id="c", title="C"))
    service.ingest("c", IngestEnvelope.model_validate(human_note("port 80 is open")))
    revision = service.read_memory("c").revision

    envelope = service.set_template(
        "c",
        TemplateChangeRequest(
            template="web",
            expected_revision=revision,
            actor="ctshen",
            reason="This is a web challenge.",
        ),
    )

    assert envelope.diff.template_changed is not None
    assert envelope.diff.template_changed.from_ == "generic@1"
    assert envelope.diff.template_changed.to == "web@1"
    assert envelope.revision_after == revision + 1
    # Nothing was added, removed, promoted or demoted — only moved.
    assert envelope.diff.fact_added == []
    assert envelope.diff.fact_removed == []
    assert envelope.diff.promoted == []
    assert envelope.diff.demoted == []
    assert service.read_memory("c").challenge.template == "web"


def test_set_template_is_attributed_in_the_journal(service: MemoryService) -> None:
    service.create_challenge(CreateChallengeRequest(id="c", title="C"))
    service.set_template(
        "c",
        TemplateChangeRequest(
            template="web", expected_revision=0, actor="ctshen", reason="binary? no, web."
        ),
    )
    records = [r for r in service.journal.records("c") if r.type == "mutation"]
    assert records[-1].actor == "ctshen"
    assert records[-1].reason == "binary? no, web."
    assert records[-1].operations == [{"op": "template", "from": "generic@1", "to": "web@1"}]


def test_mutation_into_an_unknown_section_falls_back_to_general(service: MemoryService) -> None:
    service.create_challenge(CreateChallengeRequest(id="c", title="C"))
    service.mutate(
        "c",
        MemoryMutation(
            expected_revision=0,
            actor="ctshen",
            reason="manual note",
            operations=[
                {"op": "add", "layer": "fact", "section": "not-a-real-section", "text": "hello"}
            ],
        ),
    )
    memory = service.read_memory("c")
    assert [i.text for i in memory.fact["general"]] == ["hello"]
