"""編排：注入順序、停損、中止（決定 3、4、7、8）。

全部離線 —— 假 adapter 重播真錄音，假 Memory Core 回可控的 diff。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent_bridge.adapters.fake import FakeAgentAdapter
from agent_bridge.loop import AgentLoop, LoopLimits, StopSignal
from agent_bridge.memory.fake import FakeMemoryClient
from agent_bridge.policy import FACT_ONLY, FULL, NO_MEMORY, MemoryPolicy, SessionMode

CHALLENGE = "demo-web"
FACT_DIFF = {"fact_added": ["F001"]}


async def make(
    scenario: str = "multi_tool",
    *,
    policy: MemoryPolicy = FULL,
    session: SessionMode | None = None,
    limits: LoopLimits | None = None,
    stop: StopSignal | None = None,
    diffs: tuple[dict, ...] = (),
) -> tuple[AgentLoop, FakeAgentAdapter, FakeMemoryClient]:
    adapter = FakeAgentAdapter(scenario)
    memory = FakeMemoryClient()
    await memory.create_challenge(CHALLENGE, title="Demo Web")
    if diffs:
        memory.queue_diffs(CHALLENGE, *diffs)
    loop = AgentLoop(
        adapter=adapter,
        memory=memory,
        challenge_id=CHALLENGE,
        policy=policy,
        session=session,
        limits=limits,
        stop=stop,
    )
    return loop, adapter, memory


def run(coro):
    return asyncio.run(coro)


# ---- normal 一輪 -------------------------------------------------------


def test_normal_turn_injects_board_then_ingests() -> None:
    async def scenario():
        loop, adapter, memory = await make()
        record = await loop.run_once("解這題")

        board = adapter.calls[-1]["board"]
        assert board and "F001" in board          # 第一輪給整份白板
        assert adapter.calls[-1]["prompt"] == "解這題"
        assert ("ingest", CHALLENGE) in memory.calls
        assert len(memory.ingested) == 1
        # 送出去的是合約形狀，不是內部模型
        assert memory.ingested[0]["input"]["type"] == "run_log"
        return record

    record = run(scenario())
    assert record.index == 1


def test_no_memory_injects_nothing() -> None:
    async def scenario():
        loop, adapter, memory = await make(policy=NO_MEMORY)
        await loop.run_once("解這題")
        assert adapter.calls[-1]["board"] is None
        # 完全沒去讀白板 —— 這一檔的 Agent 沒有任何管道拿到記憶
        assert not any(call[0] == "get_memory" for call in memory.calls)

    run(scenario())


def test_fact_only_board_excludes_assumptions() -> None:
    async def scenario():
        loop, adapter, _ = await make(policy=FACT_ONLY)
        await loop.run_once("解這題")
        board = adapter.calls[-1]["board"]
        assert "F001" in board and "A001" not in board

    run(scenario())


# ---- 第一輪整份、之後 diff（決定 4）------------------------------------


def test_same_session_gives_full_board_then_diff_only() -> None:
    async def scenario():
        loop, adapter, _ = await make(
            limits=LoopLimits(max_turns=2, idle_turns_before_stop=99),
            diffs=(FACT_DIFF, FACT_DIFF),
        )
        await loop.run_yolo("開始")
        return [c["board"] for c in adapter.calls]

    boards = run(scenario())
    assert len(boards) == 2
    assert "共享白板" in boards[0]        # 整份
    assert "白板異動" in boards[1]        # 只有異動
    assert "共享白板" not in boards[1]


def test_new_session_gives_full_board_every_turn() -> None:
    """每輪重開，沒有舊快照留在 context 裡，給 diff 沒有意義。"""

    async def scenario():
        loop, adapter, _ = await make(
            session=SessionMode(kind="new"),
            limits=LoopLimits(max_turns=2, idle_turns_before_stop=99),
            diffs=(FACT_DIFF, FACT_DIFF),
        )
        await loop.run_yolo("開始")
        return [c["board"] for c in adapter.calls]

    boards = run(scenario())
    assert all("共享白板" in b for b in boards)
    assert not any("白板異動" in b for b in boards)


def test_diff_turn_is_skipped_when_board_did_not_change() -> None:
    """白板沒動就沒東西可講，不要塞一段空標題進 prompt。"""

    async def scenario():
        loop, adapter, _ = await make(
            limits=LoopLimits(max_turns=2, idle_turns_before_stop=99)
        )  # 沒排 diff → 一律空
        await loop.run_yolo("開始")
        return [c["board"] for c in adapter.calls]

    boards = run(scenario())
    assert boards[1] is None


# ---- 停損（決定 8）-----------------------------------------------------


def test_stops_after_consecutive_idle_turns() -> None:
    async def scenario():
        loop, _, _ = await make(
            limits=LoopLimits(max_turns=20, idle_turns_before_stop=3)
        )
        return await loop.run_yolo("開始")

    outcome = run(scenario())
    assert len(outcome.turns) == 3
    assert "原地打轉" in outcome.stopped_because
    assert outcome.productive_turns == 0


def test_productive_turns_reset_the_idle_streak() -> None:
    async def scenario():
        loop, _, _ = await make(
            limits=LoopLimits(max_turns=10, idle_turns_before_stop=2),
            diffs=({}, FACT_DIFF),  # 空、有東西、然後一路空
        )
        return await loop.run_yolo("開始")

    outcome = run(scenario())
    # 空 → 有(歸零) → 空 → 空(停)
    assert len(outcome.turns) == 4
    assert outcome.productive_turns == 1


def test_stops_at_max_turns() -> None:
    async def scenario():
        loop, _, _ = await make(
            limits=LoopLimits(max_turns=2, idle_turns_before_stop=99)
        )
        return await loop.run_yolo("開始")

    outcome = run(scenario())
    assert len(outcome.turns) == 2
    assert "圈數上限" in outcome.stopped_because


def test_stops_at_cost_limit() -> None:
    async def scenario():
        # multi_tool 這份錄音每輪約 $0.083
        loop, _, _ = await make(
            limits=LoopLimits(max_turns=99, max_cost_usd=0.1, idle_turns_before_stop=99)
        )
        return await loop.run_yolo("開始")

    outcome = run(scenario())
    assert len(outcome.turns) == 2
    assert "花費上限" in outcome.stopped_because


# ---- 中止（決定 7）-----------------------------------------------------


def test_sentinel_file_stops_before_the_next_turn(tmp_path: Path) -> None:
    sentinel = tmp_path / "agent.stop"
    sentinel.write_text("stop", encoding="utf-8")

    async def scenario():
        loop, _, _ = await make(stop=StopSignal(sentinel))
        return await loop.run_yolo("開始")

    outcome = run(scenario())
    assert outcome.turns == []
    assert "人喊停" in outcome.stopped_because


def test_interrupted_run_stops_the_loop_but_keeps_the_work() -> None:
    """規劃書 §14：中止後已經跑完的部分仍要送進 ingest。"""

    async def scenario():
        loop, _, memory = await make("interrupted", limits=LoopLimits(max_turns=5))
        outcome = await loop.run_yolo("開始")
        return outcome, memory

    outcome, memory = run(scenario())
    assert len(outcome.turns) == 1
    assert "中斷" in outcome.stopped_because

    # 中斷前跑完的工具仍然送出去了，而且看得出它是真的執行過
    results = [e for e in memory.ingested[0]["input"]["events"] if e["kind"] == "tool_result"]
    assert any(e["outcome"] == "succeeded" for e in results)
    # 被打斷的那個誠實標成 interrupted —— R2 不會拿它當直接證據
    assert any(e["outcome"] == "interrupted" for e in results)


def test_stop_signal_clear_removes_the_file(tmp_path: Path) -> None:
    sentinel = tmp_path / "agent.stop"
    sentinel.write_text("x", encoding="utf-8")
    signal = StopSignal(sentinel)
    assert signal.raised
    signal.clear()
    assert not signal.raised
    assert not sentinel.exists()


# ---- 消融設定 ----------------------------------------------------------


def test_session_mode_disables_diff_for_new_sessions() -> None:
    policy = SessionMode(kind="new").effective(FULL)
    assert policy.diff_after_first_turn is False
    assert SessionMode(kind="same").effective(FULL).diff_after_first_turn is True


@pytest.mark.parametrize(
    "policy,expected",
    [
        (NO_MEMORY, set()),
        (FACT_ONLY, {"fact"}),
        (FULL, {"fact", "assumption", "guessing"}),
    ],
)
def test_ablation_levels_expose_the_intended_layers(policy, expected) -> None:
    assert policy.wants == expected
