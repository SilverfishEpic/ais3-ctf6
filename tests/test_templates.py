"""模板內容與 lint 的測試。

除了「現有模板要過」，更重要的是「該紅的時候真的會紅」——
一個永遠不會失敗的 lint 等於沒有 lint。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tools.lint_templates import lint_all, lint_file

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = REPO_ROOT / "templates"

VALID = {
    "id": "sample",
    "version": 1,
    "description": "測試用",
    "fact_sections": ["target", "general"],
    "assumption_sections": ["hypotheses", "general"],
    "guessing": [{"id": "SQLI", "title": "SQL Injection", "tags": ["injection"]}],
}


def write(tmp_path: Path, name: str, data: dict) -> Path:
    path = tmp_path / f"{name}.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


# ---- 現有模板 ----------------------------------------------------------


def test_shipped_templates_pass_lint() -> None:
    assert lint_all(TEMPLATE_DIR) == []


@pytest.mark.parametrize("name", ["generic", "web"])
def test_shipped_templates_have_general_sections(name: str) -> None:
    data = yaml.safe_load((TEMPLATE_DIR / f"{name}.yaml").read_text(encoding="utf-8"))
    assert "general" in data["fact_sections"]
    assert "general" in data["assumption_sections"]


def test_shared_ids_agree_across_templates() -> None:
    """跨題型共用的 ID 必須指同一件事（決定 10）。"""
    titles: dict[str, set[str]] = {}
    for path in TEMPLATE_DIR.glob("*.yaml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        for entry in data["guessing"]:
            titles.setdefault(entry["id"], set()).add(entry["title"])

    shared = {gid: t for gid, t in titles.items() if len(t) > 1}
    assert not shared, f"同一個 ID 有不同 title：{shared}"

    # 確認真的有共用的 ID，否則這個測試等於沒測到
    assert "RECON-NETWORK" in titles


# ---- lint 該紅的時候要紅 -----------------------------------------------


def test_missing_general_in_assumptions_is_caught(tmp_path: Path) -> None:
    broken = {**VALID, "assumption_sections": ["hypotheses"]}
    errors, _ = lint_file(write(tmp_path, "sample", broken))
    assert any("general" in e for e in errors)


def test_missing_general_in_facts_is_caught(tmp_path: Path) -> None:
    broken = {**VALID, "fact_sections": ["target"]}
    errors, _ = lint_file(write(tmp_path, "sample", broken))
    assert any("general" in e for e in errors)


def test_category_prefixed_id_is_caught(tmp_path: Path) -> None:
    """WEB-SQLI 這種寫法換題型時會讓 tried 狀態掉。"""
    broken = {**VALID, "guessing": [{"id": "WEB-SQLI", "title": "SQL Injection"}]}
    errors, _ = lint_file(write(tmp_path, "sample", broken))
    assert any("題型前綴" in e for e in errors)


def test_duplicate_id_within_file_is_caught(tmp_path: Path) -> None:
    broken = {
        **VALID,
        "guessing": [
            {"id": "SQLI", "title": "SQL Injection"},
            {"id": "SQLI", "title": "SQL 注入"},
        ],
    }
    errors, _ = lint_file(write(tmp_path, "sample", broken))
    assert any("重複" in e for e in errors)


def test_conflicting_titles_across_files_are_caught(tmp_path: Path) -> None:
    """同一個 ID 在兩個檔案裡意思不同 —— 換題型時會對到錯的技術。"""
    write(tmp_path, "alpha", {**VALID, "id": "alpha"})
    write(
        tmp_path,
        "beta",
        {**VALID, "id": "beta", "guessing": [{"id": "SQLI", "title": "完全不同的東西"}]},
    )
    errors = lint_all(tmp_path)
    assert any("跨檔案衝突" in e for e in errors)


def test_same_id_same_title_across_files_is_fine(tmp_path: Path) -> None:
    write(tmp_path, "alpha", {**VALID, "id": "alpha"})
    write(tmp_path, "beta", {**VALID, "id": "beta"})
    assert lint_all(tmp_path) == []


def test_id_must_match_filename(tmp_path: Path) -> None:
    errors, _ = lint_file(write(tmp_path, "sample", {**VALID, "id": "something_else"}))
    assert any("與檔名不符" in e for e in errors)


def test_missing_required_key_is_caught(tmp_path: Path) -> None:
    broken = {k: v for k, v in VALID.items() if k != "description"}
    errors, _ = lint_file(write(tmp_path, "sample", broken))
    assert any("description" in e for e in errors)


def test_bad_tags_type_is_caught(tmp_path: Path) -> None:
    broken = {**VALID, "guessing": [{"id": "SQLI", "title": "SQL Injection", "tags": "injection"}]}
    errors, _ = lint_file(write(tmp_path, "sample", broken))
    assert any("tags" in e for e in errors)


def test_unparseable_yaml_is_caught(tmp_path: Path) -> None:
    path = tmp_path / "sample.yaml"
    path.write_text("id: sample\n  bad indent: [\n", encoding="utf-8")
    errors, _ = lint_file(path)
    assert any("解析失敗" in e for e in errors)
