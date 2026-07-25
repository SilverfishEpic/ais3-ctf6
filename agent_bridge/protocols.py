"""介面定義 —— 整個 bridge 的樞紐。

Agent 那頭只認 `AgentAdapter`，所以 `FakeAgentAdapter`（重播錄音）與
`ClaudeAdapter`（真的跑）可以互換；Memory Core 還不存在也不影響開發。

對應規劃書 §13 的 adapter contract。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from agent_bridge.normalize import NormalizedRun


@dataclass
class RunResult:
    """一輪執行的產出。

    `envelope` 是要送進 `/ingest` 的東西；`run` 保留轉換後的細節，
    供 loop 做停損判斷與除錯。
    """

    envelope: dict[str, Any]
    run: NormalizedRun

    @property
    def interrupted(self) -> bool:
        return bool(self.run.run_meta.get("interrupted"))

    @property
    def cost_usd(self) -> float | None:
        return self.run.run_meta.get("total_cost_usd")


@runtime_checkable
class AgentAdapter(Protocol):
    """外部 Agent 的最小介面。

    `board` 是要注入的白板文字（決定 3：由 bridge 強制注入，不靠 Agent 自己查）。
    第一輪給整份，之後給 diff（決定 4）—— 對 adapter 來說都只是一段文字。
    """

    async def start(self, prompt: str, *, board: str | None = None) -> RunResult: ...

    async def continue_run(self, prompt: str, *, board: str | None = None) -> RunResult: ...

    async def cancel(self) -> None: ...
