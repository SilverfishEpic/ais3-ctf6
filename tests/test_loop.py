"""編排：注入順序、停損、中止、完成訊號（決定 3、4、7、8）。

全部離線 —— 假 adapter 重播真實執行紀錄，假 Memory Core 回可控的 diff。

停損的四個上限都是「跑過頭了」才踩；完成訊號是「做完了」。
後者預設關閉，所以這一檔同時要證明：開了會在解出來的那一圈停，
關了則與這個煞車存在之前**完全一樣**。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from agent_bridge.adapters.fake import FakeAgentAdapter
from agent_bridge.completion import (
    SYNTHETIC_MODEL,
    Baseline,
    CompletionDetector,
    FlagStopConfig,
    StopClass,
    StopVerdict,
    build_flag_pattern,
    sha256_token,
)
from agent_bridge.envelope import build_envelope
from agent_bridge.loop import AgentLoop, LoopLimits, LoopOutcome, StopSignal
from agent_bridge.memory.fake import FakeMemoryClient
from agent_bridge.normalize import AgentRunEvent, NormalizedRun
from agent_bridge.policy import FACT_ONLY, FULL, NO_MEMORY, MemoryPolicy, SessionMode
from agent_bridge.protocols import RunResult

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
        # multi_tool 這份紀錄每輪約 $0.083
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


# ---- 完成訊號：拿到 flag 就停 ------------------------------------------
#
# 這個煞車不存在時的實測後果（rev/SekaiCTF2022-matrix-lab-1）：第 1 圈就解掉了，
# 迴圈照樣跑到花費上限才停，$2.63 對上對照組的 $0.52，第 2、3 圈自己都說
# 「no work remains」。idle 救不了 —— 那兩圈白板都還有微幅變動。

FLAG = "SEKAI{n0t_a_r34l_flag_but_shaped_l1ke_one}"
#: summarizer 從一句描述 flag *格式* 的話產生的垃圾條目，這個 repo 的白板上真的有一條
SUMMARIZER_JUNK = "SEKAI{''; Flag suffix: ''}"
PROVIDER_529 = "API Error: 529 Overloaded"


def tool_out(content: str, *, name: str = "Bash", outcome: str = "succeeded") -> AgentRunEvent:
    return AgentRunEvent(
        seq=1, role="tool", kind="tool_result", name=name, content=content, outcome=outcome
    )


def said(content: str) -> AgentRunEvent:
    return AgentRunEvent(seq=1, role="assistant", kind="message", content=content)


def turn(*events: AgentRunEvent, **meta: Any) -> NormalizedRun:
    return NormalizedRun(events=list(events), run_meta={"model": "claude-x", **meta})


def failed_turn() -> NormalizedRun:
    """模型根本沒跑起來的一圈 —— 實測連三圈都是這樣。"""
    return turn(said(PROVIDER_529), model=SYNTHETIC_MODEL, is_error=True)


def armed(**config: Any) -> CompletionDetector:
    baseline = config.pop("baseline", None)
    config.setdefault("pattern", build_flag_pattern(["SEKAI"]))
    return CompletionDetector(FlagStopConfig(**config), baseline=baseline)


class ScriptedAdapter:
    """每輪吐一份指定的執行結果，用完之後一直重播最後一份。

    `FakeAgentAdapter` 重播的是固定紀錄檔，測不了「這一輪的工具剛好印出 flag」；
    這一個讓 flag 出現在第幾圈、由哪個頻道帶出來，都能擺佈。
    """

    def __init__(self, *runs: NormalizedRun) -> None:
        self.runs = list(runs)
        self.calls = 0

    async def start(self, prompt: str, *, board: str | None = None) -> RunResult:
        return self._next()

    async def continue_run(self, prompt: str, *, board: str | None = None) -> RunResult:
        return self._next()

    async def cancel(self) -> None:
        return None

    def _next(self) -> RunResult:
        run = self.runs[min(self.calls, len(self.runs) - 1)]
        self.calls += 1
        return RunResult(envelope=build_envelope(run, provider="scripted"), run=run)


async def scripted(
    *runs: NormalizedRun,
    completion: CompletionDetector | None = None,
    limits: LoopLimits | None = None,
    diffs: tuple[dict, ...] = (),
) -> LoopOutcome:
    memory = FakeMemoryClient()
    await memory.create_challenge(CHALLENGE, title="Demo Web")
    if diffs:
        memory.queue_diffs(CHALLENGE, *diffs)
    loop = AgentLoop(
        adapter=ScriptedAdapter(*runs),
        memory=memory,
        challenge_id=CHALLENGE,
        limits=limits or LoopLimits(max_turns=4, idle_turns_before_stop=99),
        completion=completion,
    )
    return await loop.run_yolo("開始")


def test_a_new_flag_in_tool_output_ends_the_run_on_that_turn() -> None:
    outcome = run(scripted(turn(tool_out(f"solve.py -> {FLAG}")), completion=armed()))

    assert len(outcome.turns) == 1
    assert outcome.stop_class is StopClass.SOLVED
    assert outcome.solved is True
    assert outcome.verdict is not None and outcome.verdict.flag == FLAG
    assert outcome.stopped_because.startswith("flagstop/solved: ")
    assert outcome.completion_error is None


def test_without_the_brake_the_same_solve_keeps_burning_turns() -> None:
    """這就是實測的缺陷本身：解完了還是跑到上限才停。"""
    outcome = run(scripted(turn(tool_out(f"solve.py -> {FLAG}"))))

    assert len(outcome.turns) == 4
    assert outcome.stop_class is StopClass.TURN_CAP
    assert outcome.solved is False
    assert outcome.verdict is None


def test_a_flag_that_was_already_in_the_workspace_does_not_end_the_run() -> None:
    """`test-challenge-set` 有 12/20 題的真 flag 就放在題目目錄裡，
    只做 containment 會在超過一半的題目上第 1 圈誤報成功。"""
    brake = armed(baseline=Baseline(tokens=frozenset({FLAG}), files=1))
    outcome = run(scripted(turn(tool_out(f"cat flag.txt -> {FLAG}")), completion=brake))

    assert len(outcome.turns) == 4
    assert outcome.stop_class is StopClass.TURN_CAP
    assert outcome.solved is False
    assert [item.reason for item in brake.rejected][:1] == ["already in the workspace at t=0"]


def test_the_summarizer_junk_token_does_not_end_the_run() -> None:
    """`SEKAI{''; Flag suffix: ''}` 是一句描述 flag 格式的話，不是 flag。"""
    brake = armed()
    outcome = run(scripted(turn(tool_out(f"board says {SUMMARIZER_JUNK}")), completion=brake))

    assert len(outcome.turns) == 4
    assert outcome.stop_class is StopClass.TURN_CAP
    assert [item.token for item in brake.rejected][:1] == [SUMMARIZER_JUNK]


def test_a_flag_in_assistant_prose_does_not_end_the_run() -> None:
    """Agent 說自己拿到 flag 不是證據 —— 它可以在什麼都沒跑的情況下這樣講。"""
    brake = armed()
    outcome = run(scripted(turn(said(f"Done! The flag is {FLAG}")), completion=brake))

    assert len(outcome.turns) == 4
    assert outcome.stop_class is StopClass.TURN_CAP
    assert brake.sightings == []


def test_the_memory_tool_reading_a_flag_off_the_board_does_not_end_the_run() -> None:
    """記憶工具的輸出就是白板，而白板正是我們拒絕閱讀的那個頻道。"""
    brake = armed()
    board_dump = turn(tool_out(FLAG, name="mcp__memory__memory_show"))
    outcome = run(scripted(board_dump, completion=brake))

    assert len(outcome.turns) == 4
    assert brake.sightings == []


def test_the_expected_flag_ends_the_run_as_verified() -> None:
    brake = armed(expect_sha256=sha256_token(FLAG))
    outcome = run(scripted(turn(tool_out(f"got it: {FLAG}")), completion=brake))

    assert len(outcome.turns) == 1
    assert outcome.stop_class is StopClass.SOLVED
    assert outcome.verdict is not None and outcome.verdict.verified is True


def test_a_different_token_does_not_satisfy_the_expected_flag() -> None:
    brake = armed(expect_sha256=sha256_token(FLAG))
    outcome = run(scripted(turn(tool_out("SEKAI{a_d1fferent_fake_flag}")), completion=brake))

    assert len(outcome.turns) == 4
    assert outcome.stop_class is StopClass.TURN_CAP
    assert outcome.solved is False


def test_the_expected_flag_read_off_disk_stops_but_does_not_score() -> None:
    brake = armed(
        expect_sha256=sha256_token(FLAG), baseline=Baseline(tokens=frozenset({FLAG}), files=1)
    )
    outcome = run(scripted(turn(tool_out(f"cat flag.txt -> {FLAG}")), completion=brake))

    assert len(outcome.turns) == 1
    assert outcome.stop_class is StopClass.LEAKED
    assert outcome.solved is False


def test_one_provider_failure_ends_nothing() -> None:
    """529 會偶發，一圈就停等於把整個 run 賠給一次瞬斷。"""
    outcome = run(scripted(failed_turn(), turn(tool_out("ls -la")), completion=armed()))

    assert len(outcome.turns) == 4
    assert outcome.stop_class is StopClass.TURN_CAP


def test_two_consecutive_provider_failures_end_the_run() -> None:
    outcome = run(scripted(failed_turn(), completion=armed()))

    assert len(outcome.turns) == 2
    assert outcome.stop_class is StopClass.PROVIDER_ERROR
    assert outcome.solved is False
    assert outcome.verdict is not None and outcome.verdict.flag is None


def test_a_provider_failed_turn_is_reported_without_moving_productive_turns() -> None:
    """模型沒跑起來是**看得見的事實**，但它刻意不改 `productive_turns`。

    那個數字是封存的消融實驗逐 run 記下來的共變量（實測那一次記到
    `turns=3 productive_turns=3`）。沒有人開啟任何東西的情況下讓它改變，
    等於回頭改對照組的數據 —— 完成訊號預設關閉的理由一字不改地適用於它。
    """
    outcome = run(
        scripted(
            failed_turn(),
            limits=LoopLimits(max_turns=2, idle_turns_before_stop=99),
            diffs=(FACT_DIFF, FACT_DIFF),
        )
    )

    assert len(outcome.turns) == 2
    assert all(t.changed_board for t in outcome.turns)
    assert all(t.provider_failed for t in outcome.turns)
    assert outcome.productive_turns == 2


def test_a_turn_that_worked_before_erroring_does_not_look_like_a_dead_provider() -> None:
    """`error_during_execution` 是 SDK 的萬用桶：跑了一堆工具才撞到 stream 錯誤的圈，
    設的旗標與模型從來沒啟動的那一圈一模一樣，不能連兩圈就把 run 收掉。"""
    worked_then_died = turn(
        tool_out("Disassembly of section .text:"),
        is_error=True,
        subtype="error_during_execution",
    )
    outcome = run(scripted(worked_then_died, completion=armed()))

    assert len(outcome.turns) == 4
    assert outcome.stop_class is StopClass.TURN_CAP
    assert not any(t.provider_failed for t in outcome.turns)


def test_the_board_read_off_disk_by_bash_is_suppressed_by_the_baseline() -> None:
    """第 1 層是靠工具名稱排除白板的，`cat runs/<id>/state.yaml` 逃得掉那一層。

    擋下它的是第 2 層 —— 前提是 `runs/` 有被 t=0 掃描看到（見 `SKIP_DIRS`）。
    """
    brake = armed(baseline=Baseline(tokens=frozenset({FLAG}), files=1))
    board_on_disk = turn(tool_out(f"cat runs/demo-web/state.yaml\n  - {FLAG}", name="Bash"))
    outcome = run(scripted(board_on_disk, completion=brake))

    assert len(outcome.turns) == 4
    assert outcome.solved is False
    assert [item.reason for item in brake.rejected][:1] == ["already in the workspace at t=0"]


def test_the_flag_wins_over_an_interrupted_turn() -> None:
    """工具印出了 flag 是關於題目的事實；中不中斷只是這一圈怎麼結束的。"""
    solved_then_cancelled = turn(tool_out(f"solve.py -> {FLAG}"), interrupted=True)
    outcome = run(scripted(solved_then_cancelled, completion=armed()))

    assert len(outcome.turns) == 1
    assert outcome.stop_class is StopClass.SOLVED


def test_a_broken_brake_never_kills_the_run() -> None:
    """煞車故障不能害死一圈真金白銀的執行 —— 但也不能安靜地消失。"""

    class ExplodingDetector(CompletionDetector):
        def observe(self, run: NormalizedRun, *, turn: int) -> StopVerdict | None:
            raise RuntimeError("brake exploded")

    outcome = run(scripted(turn(tool_out(FLAG)), completion=ExplodingDetector()))

    assert len(outcome.turns) == 4
    assert outcome.stop_class is StopClass.TURN_CAP
    assert outcome.completion_error == "RuntimeError: brake exploded"


# ---- 煞車關掉時，行為與加它之前完全相同 --------------------------------


def test_brake_off_still_stops_at_the_cost_limit() -> None:
    async def scenario():
        loop, _, _ = await make(
            limits=LoopLimits(max_turns=99, max_cost_usd=0.1, idle_turns_before_stop=99)
        )
        return await loop.run_yolo("開始")

    outcome = run(scenario())
    assert len(outcome.turns) == 2
    assert "花費上限" in outcome.stopped_because
    assert outcome.stop_class is StopClass.BUDGET
    assert outcome.solved is False
    assert outcome.verdict is None


def test_brake_off_still_stops_after_consecutive_idle_turns() -> None:
    async def scenario():
        loop, _, _ = await make(limits=LoopLimits(max_turns=20, idle_turns_before_stop=3))
        return await loop.run_yolo("開始")

    outcome = run(scenario())
    assert len(outcome.turns) == 3
    assert "原地打轉" in outcome.stopped_because
    assert outcome.stop_class is StopClass.IDLE
    assert outcome.productive_turns == 0
    assert outcome.solved is False


def test_the_other_brakes_all_get_a_stable_stop_class(tmp_path: Path) -> None:
    """`stopped_because` 是給人看的中文，解析不了；統計要的是 `stop_class`。"""

    async def turn_cap():
        loop, _, _ = await make(limits=LoopLimits(max_turns=2, idle_turns_before_stop=99))
        return await loop.run_yolo("開始")

    async def interrupted():
        loop, _, _ = await make("interrupted", limits=LoopLimits(max_turns=5))
        return await loop.run_yolo("開始")

    async def human_stop():
        sentinel = tmp_path / "agent.stop"
        sentinel.write_text("stop", encoding="utf-8")
        loop, _, _ = await make(stop=StopSignal(sentinel))
        return await loop.run_yolo("開始")

    classes = [run(scenario()).stop_class for scenario in (turn_cap, interrupted, human_stop)]
    assert classes == [StopClass.TURN_CAP, StopClass.INTERRUPTED, StopClass.HUMAN_STOP]
    assert not any(cls.counts_as_solved for cls in classes)


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
