"""把紀錄檔用人看得懂的方式印出來。

錄完之後要能一眼確認錄到的東西對不對，M1 寫轉換時也要一直回頭看形狀。

    python tools/inspect_fixture.py                     # 全部，摘要
    python tools/inspect_fixture.py single_tool         # 單一，詳細
    python tools/inspect_fixture.py --all-fields tool_failure
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "fixtures" / "claude"

WIDTH = 120


def clip(value: object, limit: int = 100) -> str:
    text = str(value).replace("\n", "\\n")
    return text if len(text) <= limit else text[:limit] + f"…(+{len(text) - limit})"


def show_block(block: dict, indent: str = "      ") -> None:
    kind = block.get("__type__", "?")
    if kind == "TextBlock":
        print(f"{indent}TextBlock        {clip(block.get('text'))}")
    elif kind == "ThinkingBlock":
        thinking = block.get("thinking") or ""
        print(f"{indent}ThinkingBlock    len={len(thinking)} signed={bool(block.get('signature'))}")
    elif kind == "ToolUseBlock":
        print(f"{indent}ToolUseBlock     id={block.get('id')} name={block.get('name')}")
        print(f"{indent}                 input={clip(block.get('input'))}")
    elif kind == "ToolResultBlock":
        print(
            f"{indent}ToolResultBlock  for={block.get('tool_use_id')} "
            f"is_error={block.get('is_error')}"
        )
        print(f"{indent}                 content={clip(block.get('content'))}")
    else:
        print(f"{indent}{kind:<16} {clip(block)}")


def show_row(row: dict, all_fields: bool) -> None:
    data = row["data"]
    header = f"[{row['seq']:>2}] {row['mono']:>7.2f}s  {row['kind']}/{row['type']}"
    print(header)

    if all_fields:
        print("      " + clip(json.dumps(data, ensure_ascii=False), 2000))
        return

    if row["kind"] == "meta":
        for key, value in data.items():
            print(f"      {key}: {clip(value)}")
        return

    if row["kind"] == "hook":
        print(f"      tool={data.get('tool_name')}  tool_use_id={data.get('tool_use_id')}")
        print(f"      input={clip(data.get('tool_input'))}")
        if data.get("duration_ms") is not None:
            print(f"      duration_ms={data['duration_ms']:.1f}")
        if data.get("tool_response") is not None:
            print(f"      response={clip(data.get('tool_response'), 200)}")
        if data.get("error") is not None:
            error = clip(data.get("error"), 200)
            interrupted = data.get("is_interrupt")
            print(f"      error={error}  is_interrupt={interrupted}")
        return

    kind = row["type"]
    if kind in ("AssistantMessage", "UserMessage"):
        content = data.get("content")
        if isinstance(content, str):
            print(f"      (text) {clip(content)}")
        else:
            for block in content or []:
                show_block(block)
        if kind == "AssistantMessage":
            print(f"      model={data.get('model')} stop_reason={data.get('stop_reason')}")
    elif kind == "ResultMessage":
        print(
            f"      subtype={data.get('subtype')} turns={data.get('num_turns')} "
            f"is_error={data.get('is_error')} stop_reason={data.get('stop_reason')}"
        )
        print(
            f"      cost_usd={data.get('total_cost_usd')} "
            f"duration_ms={data.get('duration_ms')} terminal_reason={data.get('terminal_reason')}"
        )
        if data.get("permission_denials"):
            print(f"      permission_denials={clip(data['permission_denials'], 300)}")
    elif kind == "SystemMessage":
        print(f"      subtype={data.get('subtype')} keys={sorted((data.get('data') or {}).keys())}")
    else:
        print(f"      {clip(data, 300)}")


def load(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def summarize(path: Path) -> None:
    rows = load(path)
    counts: dict[str, int] = {}
    for row in rows:
        key = f"{row['kind']}/{row['type']}"
        counts[key] = counts.get(key, 0) + 1
    span = rows[-1]["mono"] if rows else 0.0
    print(f"{path.stem:<16} {len(rows):>3} 筆  {span:>6.1f}s  " + "  ".join(
        f"{k}×{v}" for k, v in sorted(counts.items())
    ))


def main() -> int:
    parser = argparse.ArgumentParser(description="檢視紀錄檔")
    parser.add_argument("name", nargs="?", help="fixture 名稱；不給就全部列摘要")
    parser.add_argument("--all-fields", action="store_true", help="印出完整 JSON")
    args = parser.parse_args()

    if not FIXTURE_DIR.exists():
        print(f"找不到 {FIXTURE_DIR}，先跑 tools/record_run.py")
        return 1

    if not args.name:
        for path in sorted(FIXTURE_DIR.glob("*.jsonl")):
            summarize(path)
        return 0

    path = FIXTURE_DIR / f"{args.name}.jsonl"
    if not path.exists():
        print(f"沒有 {path}")
        return 1

    print("=" * WIDTH)
    print(path)
    print("=" * WIDTH)
    for row in load(path):
        show_row(row, args.all_fields)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
