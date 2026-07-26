"""給 Person 1 / Person 2 的示範:不需要 Claude 就能拿到 IngestEnvelope。

    python tools/demo_fake_adapter.py                 # 所有執行紀錄的摘要
    python tools/demo_fake_adapter.py multi_tool      # 看完整的 envelope
    python tools/demo_fake_adapter.py sandbox_denied --events

P1:`--json` 印出來的東西就是 POST /ingest 的 body。
P2:把 `ctfmem agent run` 底下換成 FakeAgentAdapter 就能離線開發。

**`run_meta` 不在 envelope 裡。** `RunLogInput` 是 `additionalProperties: false`,
只准 `type` 與 `events`,所以成本、耗時、`terminal_reason` 這些留在本地的
`NormalizedRun.run_meta`,給 loop 做停損與除錯用:

    adapter  = FakeAgentAdapter("multi_tool")
    envelope = adapter.run_sync()          # 上線的:只有 source + input
    meta     = adapter.load_run().run_meta  # 本地的:成本/耗時/收尾狀態

這支腳本兩邊都印,並且標清楚哪邊是哪邊。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from agent_bridge.adapters.fake import FakeAgentAdapter  # noqa: E402

WIRE_ONLY = "── 以下是上線的 envelope ──"
LOCAL_ONLY = "── 以下**不上線**,只留在 NormalizedRun.run_meta ──"


def summarize_all() -> None:
    print(f"{'執行紀錄':<18} {'事件':>4}  結局分佈")
    print("-" * 96)
    for name in FakeAgentAdapter.available():
        adapter = FakeAgentAdapter(name)
        events = adapter.run_sync()["input"]["events"]
        meta = adapter.load_run().run_meta

        counts: dict[str, int] = {}
        for event in events:
            key = event["kind"]
            if event.get("outcome"):
                key += ":" + event["outcome"]
            counts[key] = counts.get(key, 0) + 1

        spread = "  ".join(f"{k}×{v}" for k, v in sorted(counts.items()))
        print(f"{name:<18} {len(events):>4}  {spread}")
        print(
            f"{'':<18} {'':>4}  （本地）terminal={meta.get('terminal_reason')} "
            f"interrupted={meta.get('interrupted')} "
            f"turns={meta.get('num_turns')} cost=${meta.get('total_cost_usd')}"
        )


def show(name: str, *, as_json: bool, events_only: bool) -> None:
    adapter = FakeAgentAdapter(name)
    envelope = adapter.run_sync()

    if as_json:
        print(json.dumps(envelope, ensure_ascii=False, indent=2))
        return

    run = adapter.load_run()
    local = {e.seq: e for e in run.events}

    print(WIRE_ONLY)
    print("source:", json.dumps(envelope["source"], ensure_ascii=False))
    print()
    for event in envelope["input"]["events"]:
        head = f"[{event['seq']:>2}] {event['role']}/{event['kind']}"
        if event.get("outcome"):
            head += f" ({event['outcome']})"
        if event.get("name"):
            head += f" {event['name']}"
        print(head)
        content = event["content"].replace("\n", "\n      ")
        print(f"      {content[:400]}")

        # 內部模型上還有這些,但合約沒有位置 —— 印出來讓人知道橋這邊有什麼
        internal = local.get(event["seq"])
        extras = {}
        if internal is not None:
            for key in ("truncated", "original_bytes", "duration_ms", "is_error"):
                value = getattr(internal, key, None)
                if value not in (None, False):
                    extras[key] = value
        if extras:
            print(f"      -- 本地欄位:{extras}")
        print()

    if not events_only:
        print(LOCAL_ONLY)
        print(json.dumps(run.run_meta, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="示範 FakeAgentAdapter")
    parser.add_argument("scenario", nargs="?", help="不給就列全部的摘要")
    parser.add_argument("--json", action="store_true", help="印出完整 envelope JSON")
    parser.add_argument("--events", action="store_true", help="只印事件，不印 run_meta")
    args = parser.parse_args()

    if not args.scenario:
        summarize_all()
        return 0

    available = FakeAgentAdapter.available()
    if args.scenario not in available:
        print(f"沒有 {args.scenario}。可用的：{', '.join(available)}")
        return 1

    show(args.scenario, as_json=args.json, events_only=args.events)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
