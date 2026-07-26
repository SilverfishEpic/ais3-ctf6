"""把錄下來的執行紀錄裡的本機絕對路徑打碼。

為什麼只打碼路徑
----------------
紀錄檔裡唯一會洩漏使用者身分的是 `cwd` —— 它帶了 Windows 使用者名稱與本機
目錄結構,而且換一台機器就沒有意義。其餘欄位(真實用量、花費、耗時、Claude
的實際輸出)**是這份資料的全部價值所在,一個都不能動**。

session_id / uuid 是本機 session 的識別碼,不是憑證,留著。

用法::

    python tools/redact_fixtures.py --check    # 只報告,不寫檔
    python tools/redact_fixtures.py            # 就地改寫
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "fixtures" / "claude"

def _both_separators(path: str) -> tuple[str, str]:
    return path, path.replace("\\", "/")


#: 順序有意義 —— 長的先換,否則 repo 根目錄會先被家目錄那條吃掉一半。
#:
#: `<project>` 是 Claude 自己把 repo 路徑壓成的識別字串,出現在 `memory_paths`
#: 這類 SystemMessage 欄位裡,它一樣帶著使用者名稱。
RULES: tuple[tuple[str, str], ...] = (
    *((root, "<workspace>") for root in _both_separators(str(REPO_ROOT))),
    (str(REPO_ROOT).replace(":", "-").replace("\\", "-").replace("/", "-"), "<project>"),
    *((home, "<home>") for home in _both_separators(str(Path.home()))),
)


def redact_text(value: str) -> str:
    for needle, placeholder in RULES:
        if needle in value:
            value = value.replace(needle, placeholder)
    return value


def walk(node: Any) -> tuple[Any, int]:
    """回 (打碼後的節點, 改了幾處)。"""
    if isinstance(node, str):
        new = redact_text(node)
        return new, int(new != node)
    if isinstance(node, list):
        out, hits = [], 0
        for item in node:
            value, n = walk(item)
            out.append(value)
            hits += n
        return out, hits
    if isinstance(node, dict):
        out, hits = {}, 0
        for key, item in node.items():
            value, n = walk(item)
            out[key] = value
            hits += n
        return out, hits
    return node, 0


def dump(row: dict[str, Any]) -> str:
    """跟 record_run.py 一樣的寫法,不打碼時必須 byte 級還原。"""
    return json.dumps(row, ensure_ascii=False)


def process(path: Path, *, write: bool) -> tuple[int, bool]:
    """回 (打碼處數, round-trip 是否無損)。"""
    original = path.read_text(encoding="utf-8")
    lines = [line for line in original.splitlines() if line.strip()]

    rows = [json.loads(line) for line in lines]

    # 先確認「不打碼就重寫」能還原成原檔,否則這支腳本會悄悄改壞格式
    lossless = "\n".join(dump(row) for row in rows) + "\n" == original

    redacted, hits = zip(*(walk(row) for row in rows), strict=True) if rows else ((), ())
    total = sum(hits)

    if write and total:
        path.write_text("\n".join(dump(row) for row in redacted) + "\n", encoding="utf-8")

    return total, lossless


def main() -> int:
    parser = argparse.ArgumentParser(description="把執行紀錄裡的本機路徑打碼")
    parser.add_argument("--check", action="store_true", help="只報告,不寫檔")
    parser.add_argument("--dir", default=str(FIXTURE_DIR))
    args = parser.parse_args()

    directory = Path(args.dir)
    paths = sorted(directory.glob("*.jsonl"))
    if not paths:
        print(f"{directory} 裡沒有紀錄檔")
        return 1

    problems = 0
    for path in paths:
        total, lossless = process(path, write=not args.check)
        flag = "✓" if lossless else "✗ round-trip 不無損,已跳過寫入"
        if not lossless:
            problems += 1
        action = "會打碼" if args.check else "已打碼"
        print(f"  {flag} {path.name:<22} {action} {total} 處")

    if problems:
        print(f"\n{problems} 個檔案的 JSON 重寫不是無損的,沒有動它們")
        return 1

    print(f"\n{len(paths)} 個檔案處理完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
