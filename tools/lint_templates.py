"""模板內容檢查。

分工提醒：template 的 **loader / schema / reclassification 演算法是 Person 1 的**
（規劃書 §16）。這支只檢查**我負責的內容**有沒有違反已知的約束，
不是要取代 P1 的 loader。

檢查項目
--------
1. YAML 解析得動，必要欄位齊全
2. `fact.general` 與 `assumption.general` 都存在（§7.1 強制）
3. 分節名稱不重複
4. Guess ID 在單一檔案內唯一
5. **同一個 Guess ID 跨檔案時 title 必須一致**（決定 10 的核心不變量）
6. Guess ID 沒有題型前綴（`WEB-SQLI` 這種寫法會讓 tried 狀態掉）

    python tools/lint_templates.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = REPO_ROOT / "templates"

REQUIRED_KEYS = ("id", "version", "description", "fact_sections", "assumption_sections", "guessing")

# 已知的題型名稱。Guess ID 以這些開頭就是加了題型前綴，會破壞跨題型的 tried 狀態。
CATEGORY_PREFIXES = ("WEB-", "PWN-", "REV-", "CRYPTO-", "FORENSICS-", "MISC-", "GENERIC-")


def lint_file(path: Path) -> tuple[list[str], dict[str, str]]:
    """回 (錯誤訊息, 這個檔案裡的 {guess_id: title})。"""
    errors: list[str] = []
    titles: dict[str, str] = {}

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return [f"{path.name}: YAML 解析失敗 — {exc}"], {}

    if not isinstance(data, dict):
        return [f"{path.name}: 最外層必須是 mapping"], {}

    for key in REQUIRED_KEYS:
        if key not in data:
            errors.append(f"{path.name}: 缺少必要欄位 `{key}`")

    if data.get("id") and data["id"] != path.stem:
        errors.append(f"{path.name}: id `{data['id']}` 與檔名不符")

    for field in ("fact_sections", "assumption_sections"):
        sections = data.get(field)
        if not isinstance(sections, list) or not sections:
            errors.append(f"{path.name}: `{field}` 必須是非空清單")
            continue
        if "general" not in sections:
            errors.append(
                f"{path.name}: `{field}` 少了 `general`。"
                "重新分類時對應不到的項目沒地方放（§7.1）"
            )
        duplicates = {s for s in sections if sections.count(s) > 1}
        if duplicates:
            errors.append(f"{path.name}: `{field}` 有重複分節 {sorted(duplicates)}")

    guessing = data.get("guessing")
    if not isinstance(guessing, list) or not guessing:
        errors.append(f"{path.name}: `guessing` 必須是非空清單")
        return errors, titles

    for index, entry in enumerate(guessing):
        where = f"{path.name}: guessing[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{where}: 必須是 mapping")
            continue

        guess_id = entry.get("id")
        title = entry.get("title")
        if not guess_id:
            errors.append(f"{where}: 缺少 `id`")
            continue
        if not title:
            errors.append(f"{where} ({guess_id}): 缺少 `title`")

        if guess_id in titles:
            errors.append(f"{where}: id `{guess_id}` 在同一個檔案裡重複")
        else:
            titles[guess_id] = title or ""

        if str(guess_id) != str(guess_id).upper():
            errors.append(f"{where}: id `{guess_id}` 請用大寫")

        for prefix in CATEGORY_PREFIXES:
            if str(guess_id).startswith(prefix):
                errors.append(
                    f"{where}: id `{guess_id}` 帶了題型前綴。"
                    "換題型時 tried 狀態會掉，請用技術本身命名（決定 10）"
                )

        tags = entry.get("tags")
        if tags is not None and (
            not isinstance(tags, list) or not all(isinstance(t, str) for t in tags)
        ):
            errors.append(f"{where} ({guess_id}): `tags` 必須是字串清單")

    return errors, titles


def lint_all(directory: Path) -> list[str]:
    paths = sorted(directory.glob("*.yaml"))
    if not paths:
        return [f"{directory} 裡沒有模板"]

    errors: list[str] = []
    seen: dict[str, tuple[str, str]] = {}  # id -> (title, 來自哪個檔)

    for path in paths:
        file_errors, titles = lint_file(path)
        errors.extend(file_errors)

        for guess_id, title in titles.items():
            if guess_id in seen:
                previous_title, previous_file = seen[guess_id]
                if previous_title != title:
                    errors.append(
                        f"跨檔案衝突：`{guess_id}` 在 {previous_file} 是「{previous_title}」，"
                        f"在 {path.name} 是「{title}」。"
                        "同一個 ID 必須指同一件事，否則換題型時 tried 狀態會對到錯的技術"
                    )
            else:
                seen[guess_id] = (title, path.name)

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="檢查模板內容")
    parser.add_argument("--dir", default=str(TEMPLATE_DIR))
    args = parser.parse_args()

    directory = Path(args.dir)
    errors = lint_all(directory)

    paths = sorted(directory.glob("*.yaml"))
    if errors:
        for error in errors:
            print(f"  ✗ {error}")
        print(f"\n{len(paths)} 個模板，{len(errors)} 個問題")
        return 1

    for path in paths:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        print(f"  ✓ {path.name:<16} {len(data['guessing']):>2} 個 guess")
    print(f"\n{len(paths)} 個模板全部通過")
    return 0


if __name__ == "__main__":
    sys.exit(main())
