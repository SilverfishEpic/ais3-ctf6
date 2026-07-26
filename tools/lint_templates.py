"""模板內容檢查。

分工提醒:template 的 **loader / schema / reclassification 演算法是 Person 1 的**
(規劃書 §16)。這支不另立一套規則 —— 結構直接用凍結的
`spec/template.schema.json` 驗,只補幾條 schema 表達不了的語意檢查。

檢查項目
--------
1. 結構符合 `spec/template.schema.json`
2. `id` 與檔名相同
3. `fact_sections` / `assumption_sections` 都含 `general`(§7.1 強制)
4. 分節名稱不重複
5. Guess ID 在單一檔案內唯一
6. **同一個 Guess ID 跨檔案時 title 必須一致**

關於題型前綴
------------
這支曾經**禁止** `WEB-SQLI` 這種帶題型前綴的寫法,理由是規劃書 §7.3.6
「相同 technique ID 可保留 tried state」—— 前綴會讓同一個技術在不同題型底下
變成不同 ID。

那條規則是錯的,已經移除:`spec/template.schema.json` 的
`^[A-Z0-9]+-[A-Z0-9-]+$` **強制**要有前綴段,起點文件 §6 與規劃書 §7.1 的範例
也都寫 `WEB-SQLI`。合約贏。

代價要講清楚,所以這支會把跨檔共用的 ID 數量印出來 —— 目前是 0,也就是
**§7.3.6 那條規則在現行命名下永遠不會觸發**。要嘛改命名,要嘛承認它是 no-op,
這是寫 Spec 時要裁的。

    python tools/lint_templates.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = REPO_ROOT / "templates"
SCHEMA_PATH = REPO_ROOT / "spec" / "template.schema.json"


def load_validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def lint_file(path: Path, validator: Draft202012Validator) -> tuple[list[str], dict[str, str]]:
    """回 (錯誤訊息, 這個檔案裡的 {guess_id: title})。"""
    errors: list[str] = []
    titles: dict[str, str] = {}

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return [f"{path.name}: YAML 解析失敗 — {exc}"], {}

    if not isinstance(data, dict):
        return [f"{path.name}: 最外層必須是 mapping"], {}

    # ---- 1. 結構照凍結的 schema 驗 --------------------------------
    for problem in sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path)):
        where = ".".join(str(part) for part in problem.absolute_path) or "(最外層)"
        errors.append(f"{path.name}: {where}: {problem.message}")

    # ---- 2~4. schema 表達不了的語意檢查 ----------------------------
    if data.get("id") and data["id"] != path.stem:
        errors.append(f"{path.name}: id `{data['id']}` 與檔名不符")

    for field in ("fact_sections", "assumption_sections"):
        sections = data.get(field)
        if not isinstance(sections, list):
            continue
        if "general" not in sections:
            errors.append(
                f"{path.name}: `{field}` 少了 `general`。"
                "重新分類時對應不到的項目沒地方放(§7.1)"
            )
        duplicates = {s for s in sections if sections.count(s) > 1}
        if duplicates:
            errors.append(f"{path.name}: `{field}` 有重複分節 {sorted(duplicates)}")

    # ---- 5. 單檔內 ID 唯一 ----------------------------------------
    guessing = data.get("guessing")
    if not isinstance(guessing, list):
        return errors, titles

    for index, entry in enumerate(guessing):
        if not isinstance(entry, dict):
            continue
        guess_id, title = entry.get("id"), entry.get("title")
        if not guess_id:
            continue
        if guess_id in titles:
            errors.append(f"{path.name}: guessing[{index}]: id `{guess_id}` 在同一個檔案裡重複")
        else:
            titles[guess_id] = title or ""

    return errors, titles


def lint_all(directory: Path) -> tuple[list[str], list[str]]:
    """回 (錯誤, 給人看的提醒)。"""
    paths = sorted(directory.glob("*.yaml"))
    if not paths:
        return [f"{directory} 裡沒有模板"], []

    validator = load_validator()
    errors: list[str] = []
    seen: dict[str, tuple[str, str]] = {}  # id -> (title, 來自哪個檔)
    shared: set[str] = set()

    for path in paths:
        file_errors, titles = lint_file(path, validator)
        errors.extend(file_errors)

        for guess_id, title in titles.items():
            if guess_id not in seen:
                seen[guess_id] = (title, path.name)
                continue

            # ---- 6. 跨檔同 ID 必須同 title -------------------------
            shared.add(guess_id)
            previous_title, previous_file = seen[guess_id]
            if previous_title != title:
                errors.append(
                    f"跨檔案衝突:`{guess_id}` 在 {previous_file} 是「{previous_title}」,"
                    f"在 {path.name} 是「{title}」。"
                    "同一個 ID 必須指同一件事,否則換題型時 tried 狀態會對到錯的技術"
                )

    notes: list[str] = []
    if not shared:
        notes.append(
            f"{len(seen)} 個 guess ID,跨檔共用 0 個 —— "
            "規劃書 §7.3.6「相同 technique ID 可保留 tried state」在現行命名下"
            "永遠不會觸發。寫 Spec 時要裁:改命名,或承認它是 no-op。"
        )
    else:
        notes.append(f"{len(seen)} 個 guess ID,其中 {len(shared)} 個跨檔共用:{sorted(shared)}")

    return errors, notes


def main() -> int:
    parser = argparse.ArgumentParser(description="檢查模板內容")
    parser.add_argument("--dir", default=str(TEMPLATE_DIR))
    args = parser.parse_args()

    directory = Path(args.dir)
    errors, notes = lint_all(directory)
    paths = sorted(directory.glob("*.yaml"))

    if errors:
        for error in errors:
            print(f"  ✗ {error}")
        print(f"\n{len(paths)} 個模板,{len(errors)} 個問題")
        return 1

    for path in paths:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        count = len(data.get("guessing") or [])
        print(f"  ✓ {path.name:<16} {count:>3} 個 guess")

    print(f"\n{len(paths)} 個模板全部通過")
    for note in notes:
        print(f"\n注意:{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
