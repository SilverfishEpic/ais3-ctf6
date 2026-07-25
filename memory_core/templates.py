"""Template registry, layout application and reclassification.

Memory Core owns the *contract* and the *algorithm*; the template component owns the
per-category technique content.

The governing principle for reclassification (設計規劃書 §7.3):

    **Changing the category or the layout is not the same as re-judging what is true.**

So a reclassification may move an item between sections, but it may never turn a Fact
into an Assumption, never change an ID, and never touch evidence.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from .errors import InvalidTemplate, TemplateNotFound
from .models import (
    GENERAL_SECTION,
    AssumptionItem,
    FactItem,
    GuessingState,
    GuessingTechniqueState,
    MemorySnapshot,
    TemplateDefinition,
)


class TemplateRegistry:
    """Loads and validates ``templates/*.yaml``.

    The registry is read-only at runtime: the Summarizer can select a template, but only
    a human/client may replace one, and nobody may edit a catalog through the API.
    """

    def __init__(self, templates: dict[str, TemplateDefinition]) -> None:
        self._templates = templates

    @classmethod
    def from_dir(cls, templates_dir: Path) -> TemplateRegistry:
        directory = Path(templates_dir)
        if not directory.is_dir():
            raise InvalidTemplate(f"templates directory {directory} does not exist")
        templates: dict[str, TemplateDefinition] = {}
        for path in sorted(directory.glob("*.yaml")):
            template = load_template(path)
            if template.id in templates:
                raise InvalidTemplate(f"duplicate template id {template.id!r} in {path}")
            templates[template.id] = template
        if "generic" not in templates:
            raise InvalidTemplate("a 'generic' template is required as the classification fallback")
        return cls(templates)

    def ids(self) -> list[str]:
        return sorted(self._templates)

    def all(self) -> list[TemplateDefinition]:
        return [self._templates[i] for i in self.ids()]

    def get(self, template_id: str) -> TemplateDefinition:
        try:
            return self._templates[template_id]
        except KeyError:
            raise TemplateNotFound(f"unknown template {template_id!r}", known=self.ids()) from None

    def resolve(self, catalog: str) -> TemplateDefinition:
        """Resolve a ``"web@1"`` catalog identifier back to its full definition."""

        template_id, _, version = catalog.partition("@")
        template = self.get(template_id)
        if version and int(version) != template.version:
            raise TemplateNotFound(
                f"catalog {catalog!r} does not match installed {template.catalog!r}",
                installed=template.catalog,
            )
        return template


def load_template(path: Path) -> TemplateDefinition:
    """Parse and validate one template file, enforcing the shared invariants."""

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise InvalidTemplate(f"{path} is not valid YAML: {exc}") from exc
    try:
        template = TemplateDefinition.model_validate(raw)
    except ValidationError as exc:
        raise InvalidTemplate(
            f"{path} does not match the template schema",
            errors=exc.errors(include_url=False),
        ) from exc
    validate_template(template, origin=str(path))
    return template


def validate_template(template: TemplateDefinition, *, origin: str = "<memory>") -> None:
    """Enforce the invariants every developer agreed to (共通設計起點 §6).

    ``fact.general`` and ``assumption.general`` are mandatory precisely so that
    reclassification always has a landing zone and can never drop an item on the floor.
    """

    if GENERAL_SECTION not in template.fact_sections:
        raise InvalidTemplate(f"{origin}: template {template.id!r} is missing fact.general")
    if GENERAL_SECTION not in template.assumption_sections:
        raise InvalidTemplate(f"{origin}: template {template.id!r} is missing assumption.general")
    for label, sections in (
        ("fact_sections", template.fact_sections),
        ("assumption_sections", template.assumption_sections),
    ):
        if len(set(sections)) != len(sections):
            raise InvalidTemplate(f"{origin}: template {template.id!r} has duplicate {label}")
    technique_ids = [t.id for t in template.guessing]
    if len(set(technique_ids)) != len(technique_ids):
        raise InvalidTemplate(
            f"{origin}: template {template.id!r} has duplicate guessing technique ids"
        )


def empty_layout(
    template: TemplateDefinition,
) -> tuple[dict[str, list[FactItem]], dict[str, list[AssumptionItem]]]:
    """Materialize the template's declared sections as empty buckets.

    Keeping declared-but-empty sections in ``state.yaml`` is intentional: it tells the
    Summarizer and the Human Client what the available layout is without a second lookup.
    """

    facts: dict[str, list[FactItem]] = {s: [] for s in template.fact_sections}
    assumptions: dict[str, list[AssumptionItem]] = {s: [] for s in template.assumption_sections}
    return facts, assumptions


def initial_guessing(template: TemplateDefinition) -> GuessingState:
    """Fresh guessing state: catalog identity plus one untried flag per technique."""

    return GuessingState(
        catalog=template.catalog,
        state={t.id: GuessingTechniqueState(tried=False) for t in template.guessing},
    )


def reclassify(snapshot: MemorySnapshot, template: TemplateDefinition) -> MemorySnapshot:
    """Re-lay-out a challenge under a different template.

    Guarantees, in order of importance:

    1. every Fact/Assumption ID is preserved;
    2. epistemic level is preserved — a Fact stays a Fact;
    3. only the section mapping changes, by exact section-name match;
    4. anything with no matching target section lands in ``general``;
    5. the Guessing catalog is replaced wholesale;
    6. ``tried`` state survives for technique IDs that still exist;
    7. the caller turns the before/after pair into a semantic diff.
    """

    new_facts, new_assumptions = empty_layout(template)

    fact_targets = set(template.fact_sections)
    for section, fact in snapshot.iter_facts():
        target = section if section in fact_targets else GENERAL_SECTION
        new_facts[target].append(fact.model_copy(deep=True))

    assumption_targets = set(template.assumption_sections)
    for section, assumption in snapshot.iter_assumptions():
        target = section if section in assumption_targets else GENERAL_SECTION
        new_assumptions[target].append(assumption.model_copy(deep=True))

    previous_state = snapshot.guessing.state
    carried = {
        technique.id: previous_state.get(technique.id, GuessingTechniqueState()).model_copy(
            deep=True
        )
        for technique in template.guessing
    }

    updated = snapshot.model_copy(deep=True)
    updated.challenge.template = template.id
    updated.challenge.template_version = template.version
    updated.challenge.category = template.id
    updated.fact = new_facts
    updated.assumption = new_assumptions
    updated.guessing = GuessingState(catalog=template.catalog, state=carried)
    return updated
