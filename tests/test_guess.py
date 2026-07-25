"""Guessing: template-owned, deterministic under a seed, never model-generated."""

from __future__ import annotations

from conftest import human_note

from memory_core.models import (
    GuessRequest,
    IngestEnvelope,
    MemoryMutation,
    TemplateChangeRequest,
)
from memory_core.service import MemoryService
from memory_core.templates import TemplateRegistry


def test_candidates_come_from_the_template_not_the_summarizer(
    service: MemoryService, demo: str, registry: TemplateRegistry
) -> None:
    """Even after ingesting text full of web vocabulary, the catalog is unchanged."""

    service.ingest(demo, IngestEnvelope.model_validate(human_note("SQL injection in the login")))
    response = service.guess(demo, GuessRequest(count=10, only_untried=False))

    catalog_ids = {t.id for t in registry.get("generic").guessing}
    assert {c.id for c in response.candidates} <= catalog_ids
    assert response.catalog == "generic@1"


def test_seeded_guess_is_reproducible(service: MemoryService, demo: str) -> None:
    first = service.guess(demo, GuessRequest(count=2, seed=42))
    second = service.guess(demo, GuessRequest(count=2, seed=42))
    assert [c.id for c in first.candidates] == [c.id for c in second.candidates]


def test_different_seeds_can_draw_different_sets(service: MemoryService, demo: str) -> None:
    """Not a strict inequality assertion — with a 3-item catalog collisions are normal.

    What matters is that the draw is a function of the seed alone, so re-running any seed
    reproduces its own result exactly.
    """

    for seed in (1, 2, 7, 1337):
        assert [c.id for c in service.guess(demo, GuessRequest(count=2, seed=seed)).candidates] == [
            c.id for c in service.guess(demo, GuessRequest(count=2, seed=seed)).candidates
        ]


def test_only_untried_filters_marked_techniques(service: MemoryService, demo: str) -> None:
    technique = service.guess(demo, GuessRequest(count=1, only_untried=False)).candidates[0].id
    service.mutate(
        demo,
        MemoryMutation(
            expected_revision=0,
            actor="ctshen",
            reason="already enumerated everything",
            operations=[{"op": "mark_tried", "technique": technique, "tried": True}],
        ),
    )
    untried = service.guess(demo, GuessRequest(count=10, only_untried=True))
    everything = service.guess(demo, GuessRequest(count=10, only_untried=False))

    assert technique not in {c.id for c in untried.candidates}
    assert technique in {c.id for c in everything.candidates}
    assert next(c for c in everything.candidates if c.id == technique).tried is True


def test_marking_an_unknown_technique_is_rejected(service: MemoryService, demo: str) -> None:
    import pytest

    from memory_core.errors import InvalidRequest

    with pytest.raises(InvalidRequest):
        service.mutate(
            demo,
            MemoryMutation(
                expected_revision=0,
                actor="ctshen",
                reason="typo",
                operations=[{"op": "mark_tried", "technique": "WEB-SQLI"}],
            ),
        )


def test_guess_follows_the_challenge_template(service: MemoryService, demo: str) -> None:
    service.set_template(
        demo,
        TemplateChangeRequest(
            template="web", expected_revision=0, actor="ctshen", reason="it is a web task"
        ),
    )
    response = service.guess(demo, GuessRequest(count=10, only_untried=False))
    assert response.catalog == "web@1"
    assert all(c.id.startswith("WEB-") for c in response.candidates)


def test_asking_for_more_than_the_catalog_returns_what_exists(
    service: MemoryService, demo: str, registry: TemplateRegistry
) -> None:
    response = service.guess(demo, GuessRequest(count=50, only_untried=False))
    assert len(response.candidates) == len(registry.get("generic").guessing)


def test_create_challenge_is_not_required_for_a_seed(service: MemoryService, demo: str) -> None:
    """An unseeded guess still works; it is simply not reproducible."""

    response = service.guess(demo, GuessRequest(count=1))
    assert len(response.candidates) == 1
