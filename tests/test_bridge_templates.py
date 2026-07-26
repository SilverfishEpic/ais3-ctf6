"""模板內容與 lint 的測試。

除了「現有模板要過」,更重要的是「該紅的時候真的會紅」——
一個永遠不會失敗的 lint 等於沒有 lint。

`tools/lint_templates.py` 已經改成用 `spec/template.schema.json` 驗結構,
所以這裡的「該紅」案例分兩類:schema 擋得住的(id 格式、缺必要欄位、
型別錯),以及 schema 表達不了、由 linter 自己補的(general 分節、
跨檔同 ID 同 title)。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tools.lint_templates import lint_all, lint_file, load_validator

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = REPO_ROOT / "templates"

VALID = {
    "id": "sample",
    "version": 1,
    "description": "測試用",
    "fact_sections": ["target", "general"],
    "assumption_sections": ["hypotheses", "general"],
    "guessing": [{"id": "WEB-SQLI", "title": "SQL Injection", "tags": ["injection"]}],
}


@pytest.fixture
def validator():
    return load_validator()


def write(tmp_path: Path, name: str, data: dict) -> Path:
    path = tmp_path / f"{name}.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def errors_for(tmp_path: Path, data: dict, validator, name: str = "sample") -> list[str]:
    return lint_file(write(tmp_path, name, data), validator)[0]


# ---- 現有模板 ----------------------------------------------------------


def test_shipped_templates_pass_lint() -> None:
    errors, _ = lint_all(TEMPLATE_DIR)
    assert errors == []


@pytest.mark.parametrize("name", ["generic", "web", "pwn", "rev", "crypto"])
def test_shipped_templates_have_general_sections(name: str) -> None:
    data = yaml.safe_load((TEMPLATE_DIR / f"{name}.yaml").read_text(encoding="utf-8"))
    assert "general" in data["fact_sections"]
    assert "general" in data["assumption_sections"]


def test_no_guess_id_is_shared_across_templates() -> None:
    """這是一個**絆線**,不是一個要求。

    規劃書 §7.3.6 說重新分類時「相同 technique ID 可保留 tried state」。
    現行命名全部帶題型前綴(`WEB-SQLI` / `PWN-ROP`),所以跨檔一個共用 ID
    都沒有 —— 那條規則永遠不會觸發,`memory_core/templates.py` 裡的實作
    等於死程式碼。

    這不是 bug,是**寫 Spec 時要裁的事**。哪天有人改了命名讓 ID 跨題型共用,
    這個測試會紅,提醒回頭把 §7.3.6 重新評估一次。
    """
    seen: dict[str, str] = {}
    shared: set[str] = set()
    for path in sorted(TEMPLATE_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        for entry in data.get("guessing") or []:
            if entry["id"] in seen:
                shared.add(entry["id"])
            seen[entry["id"]] = path.name

    assert not shared, (
        f"跨檔共用了 {sorted(shared)} —— §7.3.6 的 tried 保留現在有意義了,"
        "回頭確認 Spec 怎麼寫"
    )


# ---- schema 擋得住的 ---------------------------------------------------


def test_guess_id_without_prefix_is_caught(tmp_path: Path, validator) -> None:
    """`spec/template.schema.json` 強制 `^[A-Z0-9]+-[A-Z0-9-]+$`,要有前綴段。

    這條原本是反過來的:linter 曾經**禁止**前綴(規劃書 §7.3.6 的動機),
    跟凍結的 schema 直接牴觸。合約贏,規則移除。
    """
    broken = {**VALID, "guessing": [{"id": "SQLI", "title": "SQL Injection"}]}
    errors = errors_for(tmp_path, broken, validator)
    assert any("does not match" in e for e in errors)


def test_lowercase_guess_id_is_caught(tmp_path: Path, validator) -> None:
    broken = {**VALID, "guessing": [{"id": "web-sqli", "title": "SQL Injection"}]}
    assert errors_for(tmp_path, broken, validator)


def test_missing_required_key_is_caught(tmp_path: Path, validator) -> None:
    broken = {k: v for k, v in VALID.items() if k != "version"}
    errors = errors_for(tmp_path, broken, validator)
    assert any("version" in e for e in errors)


def test_bad_tags_type_is_caught(tmp_path: Path, validator) -> None:
    broken = {
        **VALID,
        "guessing": [{"id": "WEB-SQLI", "title": "SQL Injection", "tags": "injection"}],
    }
    errors = errors_for(tmp_path, broken, validator)
    assert any("tags" in e for e in errors)


def test_unknown_top_level_key_is_caught(tmp_path: Path, validator) -> None:
    """schema 是 `additionalProperties: false`,多帶欄位要擋下來。"""
    assert errors_for(tmp_path, {**VALID, "playbook": ["nope"]}, validator)


# ---- schema 表達不了、linter 自己補的 ----------------------------------


def test_missing_general_in_assumptions_is_caught(tmp_path: Path, validator) -> None:
    broken = {**VALID, "assumption_sections": ["hypotheses"]}
    errors = errors_for(tmp_path, broken, validator)
    assert any("general" in e for e in errors)


def test_missing_general_in_facts_is_caught(tmp_path: Path, validator) -> None:
    broken = {**VALID, "fact_sections": ["target"]}
    errors = errors_for(tmp_path, broken, validator)
    assert any("general" in e for e in errors)


def test_duplicate_section_is_caught(tmp_path: Path, validator) -> None:
    broken = {**VALID, "fact_sections": ["target", "target", "general"]}
    errors = errors_for(tmp_path, broken, validator)
    assert any("重複分節" in e for e in errors)


def test_duplicate_id_within_file_is_caught(tmp_path: Path, validator) -> None:
    broken = {
        **VALID,
        "guessing": [
            {"id": "WEB-SQLI", "title": "SQL Injection"},
            {"id": "WEB-SQLI", "title": "SQL 注入"},
        ],
    }
    errors = errors_for(tmp_path, broken, validator)
    assert any("重複" in e for e in errors)


def test_id_must_match_filename(tmp_path: Path, validator) -> None:
    errors = errors_for(tmp_path, {**VALID, "id": "something-else"}, validator)
    assert any("與檔名不符" in e for e in errors)


def test_unparseable_yaml_is_caught(tmp_path: Path, validator) -> None:
    path = tmp_path / "sample.yaml"
    path.write_text("id: sample\n  bad indent: [\n", encoding="utf-8")
    errors, _ = lint_file(path, validator)
    assert any("解析失敗" in e for e in errors)


# ---- 跨檔案 ------------------------------------------------------------


def test_conflicting_titles_across_files_are_caught(tmp_path: Path) -> None:
    """同一個 ID 在兩個檔案裡意思不同 —— 換題型時會對到錯的技術。"""
    write(tmp_path, "alpha", {**VALID, "id": "alpha"})
    write(
        tmp_path,
        "beta",
        {**VALID, "id": "beta", "guessing": [{"id": "WEB-SQLI", "title": "完全不同的東西"}]},
    )
    errors, _ = lint_all(tmp_path)
    assert any("跨檔案衝突" in e for e in errors)


def test_same_id_same_title_across_files_is_fine(tmp_path: Path) -> None:
    write(tmp_path, "alpha", {**VALID, "id": "alpha"})
    write(tmp_path, "beta", {**VALID, "id": "beta"})
    errors, notes = lint_all(tmp_path)
    assert errors == []
    assert any("跨檔共用" in n for n in notes)
