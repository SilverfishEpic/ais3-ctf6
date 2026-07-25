"""跑一次完整的迴圈，對真的 Memory Core。

離線測試只證明我的程式自己前後一致。這支證明**整條線接得起來**：
注入 → Agent 跑 → 轉換 → ingest → 拿 diff → 下一輪注入 diff。

先起 mock server：

    python -m memory_core --fake-summarizer --runs ./runs --templates ./templates --port 8777

然後：

    python tools/run_loop_demo.py                       # 假 Agent，YOLO 三圈
    python tools/run_loop_demo.py --policy fact_only    # 換一檔消融設定
    python tools/run_loop_demo.py --session new         # 換 session 模式
    python tools/run_loop_demo.py --claude ./workspace  # 真的跑 Claude（會花錢）
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from agent_bridge.adapters.fake import FakeAgentAdapter  # noqa: E402
from agent_bridge.errors import MemoryCoreError  # noqa: E402
from agent_bridge.loop import AgentLoop, LoopLimits  # noqa: E402
from agent_bridge.memory.http import HttpMemoryClient  # noqa: E402
from agent_bridge.policy import BY_NAME, SessionMode  # noqa: E402

RULE = "─" * 78


def show_board(label: str, board: str | None) -> None:
    print(f"  ┌─ {label}")
    if not board:
        print("  │  （沒有注入任何東西）")
    else:
        for line in board.splitlines():
            print(f"  │  {line}")
    print("  └─")


async def main_async(args: argparse.Namespace) -> int:
    policy = BY_NAME[args.policy]
    session = SessionMode(kind=args.session)

    memory = HttpMemoryClient(args.base_url)
    try:
        await memory.healthz()
    except MemoryCoreError as exc:
        print(f"連不上 Memory Core：{exc}")
        print("先起 mock server：python -m memory_core --fake-summarizer --port 8777")
        return 2

    challenge_id = args.challenge
    try:
        await memory.create_challenge(
            challenge_id,
            title="Loop demo",
            initial_input="A web login page is served on port 8080.",
        )
        print(f"建立 challenge {challenge_id}")
    except MemoryCoreError as exc:
        if exc.status != 409:
            raise
        print(f"challenge {challenge_id} 已存在，直接用")

    if args.claude:
        from agent_bridge.adapters.claude import ClaudeAdapter
        from agent_bridge.skill import TOOL_NAMES, build_memory_server

        workspace = Path(args.claude)
        workspace.mkdir(parents=True, exist_ok=True)
        servers = {}
        extra: list[str] = []
        if policy.skill_enabled:
            servers = {"memory": build_memory_server(memory, challenge_id, policy)}
            extra = list(TOOL_NAMES)
        adapter = ClaudeAdapter(
            workspace=workspace,
            mcp_servers=servers,
            extra_allowed_tools=extra,
            session_kind=session.kind,
            max_turns=8,
        )
        print(f"用真的 Claude，工作區 {workspace}")
    else:
        adapter = FakeAgentAdapter(args.scenario)
        print(f"用假 Agent，重播錄音 {args.scenario}")

    loop = AgentLoop(
        adapter=adapter,
        memory=memory,
        challenge_id=challenge_id,
        policy=policy,
        session=session,
        limits=LoopLimits(
            max_turns=args.turns, max_cost_usd=args.max_cost, idle_turns_before_stop=99
        ),
    )

    print(f"設定：policy={policy.name}  session={session.kind}  最多 {args.turns} 圈")
    print(RULE)

    outcome = await loop.run_yolo(args.prompt)

    for turn in outcome.turns:
        print(f"\n第 {turn.index} 圈")
        show_board("注入給 Agent 的白板", turn.board_injected)
        events = turn.result.envelope["input"]["events"]
        kinds: dict[str, int] = {}
        for event in events:
            key = f"{event['role']}/{event['kind']}"
            kinds[key] = kinds.get(key, 0) + 1
        print("  送出 " + "  ".join(f"{k}×{v}" for k, v in sorted(kinds.items())))
        print(
            f"  ingest: rev {turn.ingest.get('revision_before')} → "
            f"{turn.ingest.get('revision_after')}   "
            f"白板有變化={turn.changed_board}"
        )
        changed = {k: v for k, v in turn.diff.items() if v}
        if changed:
            print(f"  diff: {changed}")

    print()
    print(RULE)
    print(f"停止原因：{outcome.stopped_because}")
    print(
        f"共 {len(outcome.turns)} 圈，其中 {outcome.productive_turns} 圈讓白板長出東西，"
        f"花費 ${outcome.total_cost_usd:.4f}"
    )

    snapshot = await memory.get_memory(challenge_id)
    facts = sum(len(v) for v in (snapshot.get("fact") or {}).values())
    assumptions = sum(len(v) for v in (snapshot.get("assumption") or {}).values())
    print(f"白板現況：rev {snapshot.get('revision')}  {facts} Fact / {assumptions} Assumption")

    await memory.close()
    if args.claude:
        await adapter.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="對真的 Memory Core 跑一次迴圈")
    parser.add_argument("--base-url", default="http://127.0.0.1:8777")
    parser.add_argument("--challenge", default="loop-demo")
    parser.add_argument("--policy", default="full", choices=sorted(BY_NAME))
    parser.add_argument("--session", default="same", choices=["same", "new"])
    parser.add_argument("--scenario", default="multi_tool")
    parser.add_argument("--turns", type=int, default=3)
    parser.add_argument("--max-cost", type=float, default=5.0)
    parser.add_argument("--prompt", default="看一下這題有什麼線索。")
    parser.add_argument("--claude", metavar="WORKSPACE", help="用真的 Claude（會花錢）")
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
