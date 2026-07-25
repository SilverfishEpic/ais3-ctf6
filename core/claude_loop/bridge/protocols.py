"""介面定義 —— 整個 bridge 的樞紐。

Agent 那頭只認 `AgentAdapter`，記憶那頭只認 `MemoryClient`，
所以「重播錄音 / 真的跑 Claude」與「假白板 / 真 Memory Core」可以四種組合自由搭配。

`MemoryClient` 的方法對應 `spec/openapi.yaml`（origin/memory-system）的端點。
回傳值刻意保持成 dict：那些形狀是 Memory Core 擁有的，我這邊不該再定義一次
domain model，否則 contract 一改我就多一處要跟著改。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from pipeline.normalize import NormalizedRun


@dataclass
class RunResult:
    """一輪執行的產出。

    `envelope` 是要送進 `/ingest` 的東西；`run` 保留轉換後的完整細節，
    供 loop 做停損判斷與除錯（`outcome`、`duration_ms` 這些不上線）。
    """

    envelope: dict[str, Any]
    run: NormalizedRun

    @property
    def interrupted(self) -> bool:
        return bool(self.run.run_meta.get("interrupted"))

    @property
    def cost_usd(self) -> float | None:
        return self.run.run_meta.get("total_cost_usd")

    @property
    def tool_calls(self) -> int:
        return int(self.run.run_meta.get("tool_calls") or 0)


@runtime_checkable
class AgentAdapter(Protocol):
    """外部 Agent 的最小介面（規劃書 §13）。

    `board` 是要注入的白板文字（決定 3：由 bridge 強制注入，不靠 Agent 自己查）。
    第一輪給整份，之後給 diff（決定 4）—— 對 adapter 來說都只是一段文字。
    """

    async def start(self, prompt: str, *, board: str | None = None) -> RunResult: ...

    async def continue_run(self, prompt: str, *, board: str | None = None) -> RunResult: ...

    async def cancel(self) -> None: ...


@runtime_checkable
class MemoryClient(Protocol):
    """Memory Core 的最小介面。

    這一層**只讀與 ingest**。白板的語意寫入永遠由 Memory Core 自己做，
    人類的 mutate 走 Person 2 的 CLI，不從這裡走。
    """

    async def create_challenge(
        self,
        challenge_id: str,
        *,
        title: str | None = None,
        initial_input: str | None = None,
        template: str | None = None,
    ) -> dict[str, Any]: ...

    async def get_memory(
        self,
        challenge_id: str,
        *,
        layer: str | None = None,
        section: str | None = None,
    ) -> dict[str, Any]: ...

    async def ingest(self, challenge_id: str, envelope: dict[str, Any]) -> dict[str, Any]: ...

    async def query(
        self,
        challenge_id: str,
        question: str,
        *,
        layers: list[str] | None = None,
    ) -> dict[str, Any]: ...

    async def guess(
        self,
        challenge_id: str,
        *,
        count: int = 3,
        only_untried: bool = True,
        seed: int | None = None,
    ) -> dict[str, Any]: ...

    async def close(self) -> None: ...
