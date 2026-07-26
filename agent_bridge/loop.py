"""編排：normal 一輪、YOLO 多輪、中止與停損。

normal（規劃書 §14）::

    讀白板 → Agent 跑一輪 → ingest → 拿 diff → 停

YOLO 就是上面那圈重複，中間重讀白板。**YOLO 屬於 bridge，不屬於 Memory Core。**

白板怎麼進 Agent（決定 3、4）
- 每輪開場由 bridge 強制注入，不靠 Agent 自己查
- 第一輪整份；same-session 之後只給 diff；new-session 每輪都整份

停下來的四個理由
- 人喊停（sentinel 檔或 Ctrl-C）—— 決定 7
- 連續 N 圈 diff 全空，代表在原地打轉 —— 決定 8
- 圈數 / 花費上限
- Agent 自己被中斷

中止之後，**已經跑完的部分仍然會送進 ingest**（規劃書 §14 的三條保證）——
M0 實測確認被中斷那一輪先前跑完的工具在 collector 的 buffer 裡拿得到。
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_bridge.board import is_empty_diff, revision
from agent_bridge.inject import render_board, render_diff
from agent_bridge.policy import MemoryPolicy, SessionMode
from agent_bridge.protocols import AgentAdapter, MemoryClient, RunResult

STOP_FILE = "agent.stop"


@dataclass
class TurnRecord:
    """一輪的結果，給停損判斷與事後檢查用。"""

    index: int
    prompt: str
    board_injected: str | None
    result: RunResult
    ingest: dict[str, Any]

    @property
    def diff(self) -> dict[str, Any]:
        return self.ingest.get("diff") or {}

    @property
    def changed_board(self) -> bool:
        return not is_empty_diff(self.diff)

    @property
    def cost_usd(self) -> float:
        return self.result.cost_usd or 0.0


@dataclass
class LoopOutcome:
    turns: list[TurnRecord] = field(default_factory=list)
    stopped_because: str = "completed"

    @property
    def total_cost_usd(self) -> float:
        return sum(t.cost_usd for t in self.turns)

    @property
    def productive_turns(self) -> int:
        return sum(1 for t in self.turns if t.changed_board)


@dataclass
class LoopLimits:
    """YOLO 的煞車。全部都是上限，先到先停。"""

    max_turns: int = 10
    max_cost_usd: float | None = 5.0
    #: 連續幾圈沒讓白板長出東西就停（決定 8）
    idle_turns_before_stop: int = 3


class StopSignal:
    """跨 process 的中止旗標（決定 7）。

    `ctfmem agent stop` 是另一個 process，但規格明講不要常駐服務，
    所以用一個檔案當旗標，迴圈在每圈之間看一眼。同時掛 SIGINT。

    CLI 透過 `agent_bridge.runtime` 建立 challenge-local 路徑；預設位置是
    `.ctfmem/<challenge>/agent.stop`。
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self._interrupted = False
        self._previous: Any = None

    def install(self) -> None:
        with contextlib.suppress(ValueError, OSError):
            self._previous = signal.signal(signal.SIGINT, self._on_signal)

    def restore(self) -> None:
        if self._previous is not None:
            with contextlib.suppress(ValueError, OSError):
                signal.signal(signal.SIGINT, self._previous)
            self._previous = None

    def _on_signal(self, *_: Any) -> None:
        self._interrupted = True

    @property
    def raised(self) -> bool:
        if self._interrupted:
            return True
        return bool(self.path and self.path.exists())

    async def wait(self, *, interval: float = 0.1) -> None:
        """等到中止旗標升起。"""
        while not self.raised:
            await asyncio.sleep(interval)

    def clear(self) -> None:
        self._interrupted = False
        if self.path and self.path.exists():
            with contextlib.suppress(OSError):
                self.path.unlink()


class AgentLoop:
    """把 adapter、記憶、注入策略綁在一起。"""

    def __init__(
        self,
        *,
        adapter: AgentAdapter,
        memory: MemoryClient,
        challenge_id: str,
        policy: MemoryPolicy | None = None,
        session: SessionMode | None = None,
        limits: LoopLimits | None = None,
        stop: StopSignal | None = None,
    ) -> None:
        self.adapter = adapter
        self.memory = memory
        self.challenge_id = challenge_id
        self.session = session or SessionMode()
        adapter_session = getattr(adapter, "session_kind", None)
        if adapter_session is not None and adapter_session != self.session.kind:
            raise ValueError(
                "AgentLoop session.kind 與 adapter.session_kind 不一致："
                f"{self.session.kind!r} != {adapter_session!r}"
            )
        self.policy = self.session.effective(policy or MemoryPolicy())
        self.limits = limits or LoopLimits()
        self.stop = stop or StopSignal()
        self._turn = 0
        self._last_revision: int | None = None
        self._last_injected_revision: int | None = None
        self._last_agent_revision_after: int | None = None
        #: 上一輪 ingest 回來的 diff，下一輪要拿它當注入內容（決定 4）
        self._pending_diff: dict[str, Any] = {}

    # ---- 對外 ------------------------------------------------------

    async def run_once(self, prompt: str) -> TurnRecord:
        """normal 模式：跑一輪就回來。"""
        return await self._turn_once(prompt, first=True)

    async def run_yolo(self, prompt: str) -> LoopOutcome:
        """YOLO：一直跑到某個煞車踩下去。"""
        outcome = LoopOutcome()
        self.stop.install()
        idle_streak = 0

        try:
            for index in range(self.limits.max_turns):
                if self.stop.raised:
                    outcome.stopped_because = "人喊停"
                    break

                record = await self._turn_once_until_stopped(prompt, first=index == 0)
                outcome.turns.append(record)

                if record.result.interrupted:
                    outcome.stopped_because = "Agent 執行被中斷"
                    break

                idle_streak = 0 if record.changed_board else idle_streak + 1
                if idle_streak >= self.limits.idle_turns_before_stop:
                    outcome.stopped_because = (
                        f"連續 {idle_streak} 圈白板沒有變化，停止避免原地打轉"
                    )
                    break

                if (
                    self.limits.max_cost_usd is not None
                    and outcome.total_cost_usd >= self.limits.max_cost_usd
                ):
                    outcome.stopped_because = (
                        f"達到花費上限 ${self.limits.max_cost_usd}"
                    )
                    break

                # 第二輪起不必再重述原始要求，白板的 diff 就是這一輪的輸入
                prompt = "根據白板的最新異動繼續。"
            else:
                outcome.stopped_because = f"達到圈數上限 {self.limits.max_turns}"
        finally:
            self.stop.restore()

        return outcome

    # ---- 內部 ------------------------------------------------------

    async def _turn_once(self, prompt: str, *, first: bool) -> TurnRecord:
        self._turn += 1
        board = await self._board_for_turn(first=first)

        if first:
            result = await self.adapter.start(prompt, board=board)
        else:
            result = await self.adapter.continue_run(prompt, board=board)

        # 中止時已經跑完的部分照樣送出去（規劃書 §14）
        ingest = await self.memory.ingest(self.challenge_id, result.envelope)
        self._pending_diff = ingest.get("diff") or {}
        after = ingest.get("revision_after")
        self._last_agent_revision_after = after if isinstance(after, int) else None

        return TurnRecord(
            index=self._turn,
            prompt=prompt,
            board_injected=board,
            result=result,
            ingest=ingest,
        )

    async def _turn_once_until_stopped(self, prompt: str, *, first: bool) -> TurnRecord:
        turn_task = asyncio.create_task(self._turn_once(prompt, first=first))
        stop_task = asyncio.create_task(self.stop.wait())
        try:
            done, _ = await asyncio.wait(
                {turn_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if stop_task in done and self.stop.raised and not turn_task.done():
                await self.adapter.cancel()
            return await turn_task
        finally:
            stop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stop_task

    async def _board_for_turn(self, *, first: bool) -> str | None:
        """決定這一輪要注入什麼。"""
        if self.policy.inject == "never":
            return None

        snapshot = await self.memory.get_memory(self.challenge_id)
        current = revision(snapshot)

        give_full = first or not self.policy.diff_after_first_turn
        if (
            not give_full
            and self._last_agent_revision_after is not None
            and current != self._last_agent_revision_after
        ):
            # 兩輪之間有外部人類 mutation。沒有 diff-history endpoint 時，
            # 安全作法是刷新整份白板，避免同 session Agent 只看到自己的舊 diff。
            give_full = True

        if give_full:
            self._last_revision = current
            self._last_injected_revision = current
            candidates = await self._guess_candidates()
            text = render_board(snapshot, self.policy, guess_candidates=candidates)
            return text or None

        # 之後只給 diff（決定 4）。diff 只有 ID，要靠新的 snapshot 反查文字。
        #
        before = (
            self._last_injected_revision
            if self._last_injected_revision is not None
            else current
        )
        self._last_revision = current
        self._last_injected_revision = current
        text = render_diff(
            self._pending_diff, snapshot, self.policy, revision_before=before
        )
        # 白板沒動就沒東西可注入 —— 不要塞一段空標題進 prompt
        return text or None

    async def _guess_candidates(self) -> list[dict[str, Any]] | None:
        if not self.policy.includes("guessing"):
            return None
        result = await self.memory.guess(
            self.challenge_id,
            count=self.policy.guess_count,
            seed=self.policy.guess_seed,
        )
        return result.get("candidates")


async def run_normal(
    *,
    adapter: AgentAdapter,
    memory: MemoryClient,
    challenge_id: str,
    prompt: str,
    policy: MemoryPolicy | None = None,
) -> TurnRecord:
    """normal 模式的便利入口。"""
    loop = AgentLoop(
        adapter=adapter, memory=memory, challenge_id=challenge_id, policy=policy
    )
    return await loop.run_once(prompt)


__all__ = [
    "STOP_FILE",
    "AgentLoop",
    "LoopLimits",
    "LoopOutcome",
    "StopSignal",
    "TurnRecord",
    "run_normal",
]
