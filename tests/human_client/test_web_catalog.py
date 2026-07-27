from __future__ import annotations

from pathlib import Path

import pytest
from conftest import TEMPLATES_DIR

from human_client.web.catalog import load_catalog


def write(directory: Path, name: str, body: str) -> None:
    (directory / name).write_text(body, encoding="utf-8")


def test_the_shipped_templates_directory_loads_without_problems() -> None:
    catalog = load_catalog(TEMPLATES_DIR)

    assert catalog.problems == []
    assert len(catalog.templates) >= 1
    for template in catalog.templates:
        assert template.id
        assert template.version >= 1
        assert template.catalog == f"{template.id}@{template.version}"


def test_every_shipped_technique_carries_a_title() -> None:
    catalog = load_catalog(TEMPLATES_DIR)
    labelled = [label for template in catalog.templates for label in template.guessing]

    assert labelled
    assert all(label.title for label in labelled)


def test_a_missing_directory_is_an_operator_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        load_catalog(tmp_path / "nowhere")


def test_an_unparseable_file_becomes_a_problem_not_a_crash(tmp_path: Path) -> None:
    write(tmp_path, "broken.yaml", "id: [unclosed\n")
    write(tmp_path, "good.yaml", "id: generic\nversion: 1\n")

    catalog = load_catalog(tmp_path)

    assert [template.id for template in catalog.templates] == ["generic"]
    assert any("broken.yaml" in problem for problem in catalog.problems)


def test_a_file_without_id_or_version_is_reported_and_skipped(tmp_path: Path) -> None:
    write(tmp_path, "nameless.yaml", "version: 1\n")
    write(tmp_path, "unversioned.yaml", "id: web\n")
    write(tmp_path, "good.yaml", "id: generic\nversion: 1\n")

    catalog = load_catalog(tmp_path)

    assert [template.id for template in catalog.templates] == ["generic"]
    assert any("nameless.yaml" in problem for problem in catalog.problems)
    assert any("unversioned.yaml" in problem for problem in catalog.problems)


def test_keys_the_reader_does_not_consume_are_not_required(tmp_path: Path) -> None:
    write(tmp_path, "minimal.yaml", "id: minimal\nversion: 3\n")

    catalog = load_catalog(tmp_path)

    assert catalog.problems == []
    assert catalog.templates[0].catalog == "minimal@3"
    assert catalog.templates[0].description == ""
    assert catalog.templates[0].guessing == []


def test_a_duplicate_id_is_reported_and_the_first_file_wins(tmp_path: Path) -> None:
    write(tmp_path, "a-first.yaml", "id: web\nversion: 1\ndescription: first\n")
    write(tmp_path, "b-second.yaml", "id: web\nversion: 2\ndescription: second\n")

    catalog = load_catalog(tmp_path)

    assert [template.catalog for template in catalog.templates] == ["web@1"]
    assert any("duplicate template id" in problem for problem in catalog.problems)


def test_a_malformed_technique_costs_one_row_not_the_file(tmp_path: Path) -> None:
    write(
        tmp_path,
        "web.yaml",
        "id: web\n"
        "version: 1\n"
        "guessing:\n"
        "- id: WEB-SQLI\n"
        "  title: SQL Injection\n"
        "  tags: [database, 7]\n"
        "- id: WEB-NO-TITLE\n"
        "- title: no id\n",
    )

    catalog = load_catalog(tmp_path)
    labels = catalog.templates[0].guessing

    assert catalog.problems == []
    assert [label.id for label in labels] == ["WEB-SQLI"]
    assert labels[0].tags == ["database"]


def test_an_empty_directory_is_reported_rather_than_raised(tmp_path: Path) -> None:
    catalog = load_catalog(tmp_path)

    assert catalog.templates == []
    assert any("no usable template files" in problem for problem in catalog.problems)


def test_to_json_carries_the_catalog_identifier_the_page_pairs_on(tmp_path: Path) -> None:
    write(
        tmp_path,
        "web.yaml",
        "id: web\nversion: 1\nguessing:\n- id: WEB-SQLI\n  title: SQL Injection\n",
    )

    payload = load_catalog(tmp_path).to_json()

    assert payload["templates"][0]["catalog"] == "web@1"
    assert payload["templates"][0]["guessing"][0]["title"] == "SQL Injection"
    assert payload["directory"] == str(tmp_path)
