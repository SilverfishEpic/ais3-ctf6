"""真的跑 Claude Agent SDK。

**這是生產路徑** —— `rows_from_live()` 唯一被實際呼叫的地方，也是 M0 那六份執行紀錄
最終要對得上的東西。錄製走 `tools/record_run.py`，這裡走同一組 hook、同一個
`jsonable()`、同一個 `normalize()`，所以 fixture 測到的形狀就是這裡產生的形狀。

session 模式（規劃書 §15，第二個實驗變因）
------------------------------------------
same：整段跑下來共用一個 SDK session，Agent 保有對話歷史。白板第一輪給整份、
      之後給 diff（決定 4）。
new ：每一輪重開一個 session，沒有歷史，白板每輪給整份。
      這一組是在測「中央記憶單獨撐不撐得起跨輪記憶」。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

from agent_bridge.collect import RunCollector, merge_hooks
from agent_bridge.envelope import build_envelope
from agent_bridge.normalize import normalize, rows_from_live
from agent_bridge.protocols import RunResult
from agent_bridge.sandbox import ToolPolicy, sandbox_hooks
from agent_bridge.truncate import TruncationPolicy

SessionKind = Literal["same", "new"]

BOARD_SEPARATOR = "\n\n---\n\n"


class ClaudeAdapter:
    """`AgentAdapter` 的正式實作。"""

    def __init__(
        self,
        *,
        workspace: str | Path,
        tool_policy: ToolPolicy | None = None,
        truncation: TruncationPolicy | None = None,
        mcp_servers: dict[str, Any] | None = None,
        extra_allowed_tools: Sequence[str] = (),
        model: str | None = None,
        max_turns: int = 30,
        max_budget_usd: float | None = None,
        session_kind: SessionKind = "same",
        system_prompt: str | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.tool_policy = tool_policy or ToolPolicy()
        self.truncation = truncation or TruncationPolicy()
        self.mcp_servers = mcp_servers or {}
        self.extra_allowed_tools = list(extra_allowed_tools)
        self.model = model
        self.max_turns = max_turns
        self.max_budget_usd = max_budget_usd
        self.session_kind: SessionKind = session_kind
        self.system_prompt = system_prompt

        self._collector = RunCollector()
        self._client: ClaudeSDKClient | None = None
        self._turn = 0

    # ---- AgentAdapter ----------------------------------------------

    async def start(self, prompt: str, *, board: str | None = None) -> RunResult:
        return await self._run(prompt, board, first=True)

    async def continue_run(self, prompt: str, *, board: str | None = None) -> RunResult:
        return await self._run(prompt, board, first=False)

    async def cancel(self) -> None:
        if self._client is not None:
            await self._client.interrupt()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    # ---- 內部 ------------------------------------------------------

    def _options(self) -> ClaudeAgentOptions:
        allowed = sorted(self.tool_policy.allowed_tools) + list(self.extra_allowed_tools)
        return ClaudeAgentOptions(
            cwd=str(self.workspace),
            allowed_tools=allowed,
            # PreToolUse 白名單才是真正的關卡（決定 6）。用 bypassPermissions 是為了
            # 不要卡在互動式詢問 —— 這是非互動的執行。
            permission_mode="bypassPermissions",
            # 不載入使用者/專案的 settings，免得實驗被別的環境設定污染
            setting_sources=None,
            hooks=merge_hooks(sandbox_hooks(self.tool_policy), self._collector.hooks()),
            mcp_servers=self.mcp_servers,
            max_turns=self.max_turns,
            max_budget_usd=self.max_budget_usd,
            model=self.model,
            system_prompt=self.system_prompt,
        )

    async def _ensure_client(self, *, fresh: bool) -> ClaudeSDKClient:
        if fresh and self._client is not None:
            await self._client.disconnect()
            self._client = None
        if self._client is None:
            client = ClaudeSDKClient(options=self._options())
            await client.connect()
            self._client = client
        return self._client

    async def _run(self, prompt: str, board: str | None, *, first: bool) -> RunResult:
        self._turn += 1
        fresh = first or self.session_kind == "new"
        client = await self._ensure_client(fresh=fresh)

        full_prompt = f"{board}{BOARD_SEPARATOR}{prompt}" if board else prompt

        messages: list[Any] = []
        await client.query(full_prompt)
        async for message in client.receive_response():
            messages.append(message)

        # 這一行就是生產路徑：把 SDK 訊息與 hook 紀錄組成跟紀錄檔一樣的列
        rows = rows_from_live(messages, self._collector.drain())
        run = normalize(rows, policy=self.truncation)
        envelope = build_envelope(
            run,
            run_id=f"turn-{self._turn}",
            session_id=run.run_meta.get("session_id"),
        )
        return RunResult(envelope=envelope, run=run)

    async def __aenter__(self) -> ClaudeAdapter:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()
