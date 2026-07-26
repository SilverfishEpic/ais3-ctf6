"""介面定義 —— 整個 bridge 的樞紐。

Agent 那頭只認 `AgentAdapter`，記憶那頭只認 `MemoryClient`，
所以「重播執行紀錄 / 真的跑 Claude」與「假白板 / 真 Memory Core」可以四種組合自由搭配。

`MemoryClient` 的方法對應 `spec/openapi.yaml`（origin/memory-system）的端點。
回傳值刻意保持成 dict：那些形狀是 Memory Core 擁有的，我這邊不該再定義一次
domain model，否則 contract 一改我就多一處要跟著改。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from agent_bridge.normalize import NormalizedRun


@dataclass
class RunResult:
    """一輪執行的產出。

    `envelope` 是要送進 `/ingest` 的東西；`run` 保留轉換後的完整細節，
    供 loop 做停損判斷與除錯（`duration_ms` 等不上線欄位）。
    `outcome` 是公開 contract 的一部分，會隨 tool_result 一起送給 Memory Core。
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

    這一層**只讀與 ingest**。白板的語意寫入永遠由 Memory Core 自己做。

    `mutate` 與 `set_template` **刻意不在這個介面裡**,即使
    `HttpMemoryClient` 兩個都實作了。它們是人類 override 的路(規劃書 §8.6、
    §10.1),`AgentLoop` 只認這個 Protocol,所以它在結構上就沒有能力改白板 ——
    這比寫一行「請不要呼叫」可靠。

    要用那兩個方法的是 `tools/integration_check.py`(扮演人類跑起點文件 §10 的
    第 9、10 步),它直接持有 `HttpMemoryClient`,不透過這個介面。
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
