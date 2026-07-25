"""FakeAgentAdapter —— 重播錄音，不需要 Claude、不需要 API key、不花錢。

**這是交付給 Person 1 和 Person 2 的東西。**

重點是它吐出來的 `IngestEnvelope` 內容來自**真的 Claude 錄音**，不是編的。
編出來的乾淨資料會讓 Summarizer 對著現實中不存在的理想輸入調 prompt，
接上真的就爆。

給 P1（Memory Core）::

    from agent_bridge.adapters.fake import FakeAgentAdapter

    envelope = FakeAgentAdapter("multi_tool").run_sync()
    # POST /ingest 就吃這個

給 P2（CLI）：一樣，`ctfmem agent run` 底下換成這個就能離線開發。

可用的錄音（`FakeAgentAdapter.available()` 會列出來）：

===============  ==================================================
talk_only        只有文字，沒有工具
single_tool      一次工具呼叫，成功
multi_tool       多輪工具，全部成功
tool_failure     指令跑了但 exit 非 0
sandbox_denied   被沙箱擋下 —— 指令根本沒執行
interrupted      中途被喊停
===============  ==================================================

`sandbox_denied` 與 `interrupted` 特別重要：那兩種情況裡的 tool_result
**不是**關於題目的證據，Summarizer 不該把「rm 不在白名單」寫進白板。
事件上的 `outcome` 欄位就是給 Memory Core 判斷用的。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_bridge.envelope import build_envelope
from agent_bridge.normalize import NormalizedRun, load_rows, normalize
from agent_bridge.protocols import RunResult
from agent_bridge.truncate import TruncationPolicy

DEFAULT_FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "claude"


class FakeAgentAdapter:
    """符合 `AgentAdapter` 介面，但內容來自錄音。"""

    def __init__(
        self,
        scenario: str = "multi_tool",
        *,
        fixture_dir: str | Path | None = None,
        policy: TruncationPolicy | None = None,
    ) -> None:
        self.scenario = scenario
        self.fixture_dir = Path(fixture_dir) if fixture_dir else DEFAULT_FIXTURE_DIR
        self.policy = policy or TruncationPolicy()
        self.cancelled = False
        # 記錄收到過的 prompt 與白板，方便 P2 檢查注入邏輯有沒有生效
        self.calls: list[dict[str, Any]] = []

    # ---- 介面 ------------------------------------------------------

    async def start(self, prompt: str = "", *, board: str | None = None) -> RunResult:
        return self._replay(prompt, board, "start")

    async def continue_run(self, prompt: str = "", *, board: str | None = None) -> RunResult:
        return self._replay(prompt, board, "continue_run")

    async def cancel(self) -> None:
        self.cancelled = True

    # ---- 同步版，給不想寫 async 的測試用 ---------------------------

    def run_sync(self, prompt: str = "", *, board: str | None = None) -> dict[str, Any]:
        """直接回 `IngestEnvelope`，P1 最常用的入口。"""
        return self._replay(prompt, board, "run_sync").envelope

    # ---- 內部 ------------------------------------------------------

    def _replay(self, prompt: str, board: str | None, via: str) -> RunResult:
        self.calls.append({"via": via, "prompt": prompt, "board": board})
        run = self.load_run()
        envelope = build_envelope(run, run_id=f"fake-{self.scenario}-{len(self.calls)}")
        return RunResult(envelope=envelope, run=run)

    def load_run(self) -> NormalizedRun:
        path = self.fixture_dir / f"{self.scenario}.jsonl"
        if not path.exists():
            available = ", ".join(self.available(self.fixture_dir)) or "（沒有錄音檔）"
            raise FileNotFoundError(f"找不到錄音 {path}。可用的：{available}")
        return normalize(load_rows(path), policy=self.policy)

    @staticmethod
    def available(fixture_dir: str | Path | None = None) -> list[str]:
        directory = Path(fixture_dir) if fixture_dir else DEFAULT_FIXTURE_DIR
        if not directory.exists():
            return []
        return sorted(p.stem for p in directory.glob("*.jsonl"))
