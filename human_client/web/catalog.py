"""Labels read from the template files on disk.

Memory Core is the authority on templates: it owns the loader, the schema and the
reclassification algorithm. This module is a labelling aid and nothing more. It answers the
two questions the HTTP contract leaves unanswered:

* which template ids are installed, so the reclassification form can offer a picker;
* what a Guessing technique is *called*, since the whiteboard read returns technique ids
  and their tried flags but no titles.

Everything else comes from the API. Section lists in particular are taken from the
whiteboard response, which materializes every section the challenge's template declares,
so this module never sees them.

The reader is deliberately more permissive than the service's. It consumes ``id`` and
``version`` and treats every other key as optional, so a directory the service loads
happily can never stop this server from starting. Files it cannot use are collected in
``Catalog.problems`` and shown in the page instead of raised, because a template picker
missing one entry is a smaller failure than a whiteboard nobody can open.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class GuessingLabel:
    """The display half of a Guessing technique. Its tried flag lives in the challenge."""

    id: str
    title: str
    tags: list[str]


@dataclass(frozen=True)
class TemplateSummary:
    id: str
    version: int
    description: str
    guessing: list[GuessingLabel]

    @property
    def catalog(self) -> str:
        """The ``"web@1"`` identifier a challenge records, used to pair labels with state."""

        return f"{self.id}@{self.version}"


@dataclass(frozen=True)
class Catalog:
    directory: str
    templates: list[TemplateSummary]
    problems: list[str]

    def to_json(self) -> dict[str, Any]:
        return {
            "directory": self.directory,
            "problems": list(self.problems),
            "templates": [
                {
                    "id": template.id,
                    "version": template.version,
                    "catalog": template.catalog,
                    "description": template.description,
                    "guessing": [
                        {"id": label.id, "title": label.title, "tags": list(label.tags)}
                        for label in template.guessing
                    ],
                }
                for template in self.templates
            ],
        }


def load_catalog(templates_dir: Path) -> Catalog:
    """Read every ``*.yaml`` in ``templates_dir`` once, at startup.

    Raises ``ValueError`` only when the directory itself is unusable, which is an operator
    typo rather than a content problem. Per-file failures become ``problems`` entries.
    """

    directory = Path(templates_dir)
    if not directory.is_dir():
        raise ValueError(f"templates directory {directory} does not exist")

    templates: list[TemplateSummary] = []
    problems: list[str] = []
    seen: set[str] = set()

    for path in sorted(directory.glob("*.yaml")):
        try:
            summary = _read_template(path)
        except ValueError as exc:
            problems.append(str(exc))
            continue
        if summary.id in seen:
            problems.append(f"{path.name}: duplicate template id {summary.id!r}, ignored")
            continue
        seen.add(summary.id)
        templates.append(summary)

    if not templates:
        problems.append(f"{directory} contains no usable template files")

    return Catalog(directory=str(directory), templates=templates, problems=problems)


def _read_template(path: Path) -> TemplateSummary:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        problem = str(exc).replace("\n", " ")
        raise ValueError(f"{path.name}: not readable as YAML ({problem})") from exc

    if not isinstance(raw, dict):
        raise ValueError(f"{path.name}: top level is not a mapping")

    template_id = raw.get("id")
    if not isinstance(template_id, str) or not template_id:
        raise ValueError(f"{path.name}: missing a non-empty string 'id'")

    version = raw.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise ValueError(f"{path.name}: missing an integer 'version'")

    description = raw.get("description")

    return TemplateSummary(
        id=template_id,
        version=version,
        description=description if isinstance(description, str) else "",
        guessing=_read_guessing(raw.get("guessing")),
    )


def _read_guessing(value: object) -> list[GuessingLabel]:
    """Keep the entries that can label something; skip the rest silently.

    A malformed technique entry costs one unlabelled row in the checklist, and the row
    still renders from the challenge's own state. That is not worth refusing to boot over.
    """

    if not isinstance(value, list):
        return []

    labels: list[GuessingLabel] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        technique_id = entry.get("id")
        title = entry.get("title")
        if not isinstance(technique_id, str) or not isinstance(title, str):
            continue
        tags = entry.get("tags")
        kept = [tag for tag in tags if isinstance(tag, str)] if isinstance(tags, list) else []
        labels.append(GuessingLabel(id=technique_id, title=title, tags=kept))
    return labels
