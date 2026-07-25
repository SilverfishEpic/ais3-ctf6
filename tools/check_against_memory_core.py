"""對真的 Memory Core 送一次 envelope，看它收不收。

離線的 contract 測試（tests/test_wire_contract.py）只是我照 spec 抄的鏡像，
會漂移。**這支才是權威驗證** —— 走的是 Memory Core 自己的 Pydantic 驗證。

先在另一個終端機起 mock server：

    python -m memory_core --fake-summarizer --runs ./runs --templates ./templates --port 8777

然後：

    python tools/check_against_memory_core.py --base-url http://127.0.0.1:8777

每一份錄音都會建一個 challenge、POST 一次 ingest，並印出 Memory Core 的回應。
只要有一份被退，就代表我的轉換不合約。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from core.claude_loop.agent_bridge.adapters.fake import FakeAgentAdapter  # noqa: E402


def post(url: str, body: dict) -> tuple[int, dict | str]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw


def get(url: str) -> tuple[int, dict | str]:
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="對 Memory Core 驗證 envelope")
    parser.add_argument("--base-url", default="http://127.0.0.1:8777")
    parser.add_argument("--scenario", help="只測一個")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")

    status, body = get(f"{base}/healthz")
    if status != 200:
        print(f"連不上 Memory Core（{base}/healthz → {status}）。先把 mock server 起起來。")
        return 2
    print(f"healthz: {body}\n")

    names = [args.scenario] if args.scenario else FakeAgentAdapter.available()
    failures = 0

    for name in names:
        challenge_id = f"wire-check-{name.replace('_', '-')}"
        print("=" * 78)
        print(f"{name}  →  challenge {challenge_id}")
        print("=" * 78)

        status, body = post(
            f"{base}/v1/challenges",
            {"id": challenge_id, "title": name, "initial_input": "web login page on port 8080"},
        )
        if status not in (200, 201, 409):
            print(f"  ✗ 建 challenge 失敗 {status}: {body}")
            failures += 1
            continue

        envelope = FakeAgentAdapter(name).run_sync()
        events = envelope["input"]["events"]
        kinds = {}
        for event in events:
            key = f"{event['role']}/{event['kind']}"
            kinds[key] = kinds.get(key, 0) + 1
        print(f"  送出 {len(events)} 個事件：" + "  ".join(f"{k}×{v}" for k, v in sorted(kinds.items())))

        status, body = post(f"{base}/v1/challenges/{challenge_id}/ingest", envelope)

        if status != 200:
            failures += 1
            print(f"  ✗ ingest 被退 {status}")
            print("    " + json.dumps(body, ensure_ascii=False, indent=2)[:1500])
            continue

        diff = body.get("diff", {})
        print(f"  ✓ ingest 接受  revision {body.get('revision_before')} → {body.get('revision_after')}")
        print(f"    summary: {body.get('summary')}")
        print(f"    source_id: {body.get('source_id')}")
        changed = {k: v for k, v in diff.items() if v}
        print(f"    diff: {json.dumps(changed, ensure_ascii=False)}")

        status, memory = get(f"{base}/v1/challenges/{challenge_id}/memory")
        if status == 200:
            facts = memory.get("fact", {})
            assumptions = memory.get("assumption", {})
            n_fact = sum(len(v) for v in facts.values()) if isinstance(facts, dict) else 0
            n_assume = sum(len(v) for v in assumptions.values()) if isinstance(assumptions, dict) else 0
            print(f"    白板現況: {n_fact} 個 Fact / {n_assume} 個 Assumption")
            for section, items in (facts.items() if isinstance(facts, dict) else []):
                for item in items:
                    print(f"      FACT [{section}] {item.get('id')}: {item.get('text')}")
        print()

    print("=" * 78)
    if failures:
        print(f"{failures}/{len(names)} 份被退 —— 轉換還不合約")
        return 1
    print(f"{len(names)}/{len(names)} 份全部被 Memory Core 接受")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
