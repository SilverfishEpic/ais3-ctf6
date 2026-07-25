"""給 Agent 的記憶工具：memory_show / memory_query / memory_guess。

以 in-process 的 MCP server 提供，所以工具直接呼叫 `MemoryClient`，
不用另外起一個 process。

定位（重要）
------------
這**不是**白板進到 Agent 的主要管道。主要管道是 bridge 在每輪開場強制注入
（決定 3）—— 靠 Agent 自己想到要查，消融實驗就測不準。

這些工具是「跑到一半懷疑手上的資訊過期了」的補救。工具說明就是這樣寫的。

三個工具全部**唯讀**。Agent 不能寫白板，它的執行紀錄會自動送去整理，
由 Summarizer 決定什麼進白板。說明文字刻意把這件事講明白，
免得它在那邊找不到「儲存」的工具而空轉。
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from agent_bridge.inject import render_board
from agent_bridge.policy import MemoryPolicy
from agent_bridge.protocols import MemoryClient
from agent_bridge.sdk_compat import create_sdk_mcp_server, tool

SERVER_NAME = "memory"

#: 註冊給 `ClaudeAgentOptions.allowed_tools` 用的完整工具名稱。
TOOL_NAMES = (
    f"mcp__{SERVER_NAME}__memory_show",
    f"mcp__{SERVER_NAME}__memory_query",
    f"mcp__{SERVER_NAME}__memory_guess",
)

READ_ONLY = (
    "唯讀。你無法寫入白板 —— 你這一輪的執行紀錄會自動送去整理，"
    "由記憶服務決定哪些成為 Fact、哪些是 Assumption。"
)


def _text(payload: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": payload}]}


def build_memory_server(
    client: MemoryClient,
    challenge_id: str,
    policy: MemoryPolicy,
) -> Any:
    """做出一個綁定到特定 challenge 的 MCP server。"""

    @tool(  # type: ignore[untyped-decorator]
        "memory_show",
        "顯示共享白板目前的完整內容。開場已經給過你一份，這個工具是用來"
        "確認手上的資訊有沒有過期。" + READ_ONLY,
        {},
    )
    async def memory_show(args: dict[str, Any]) -> dict[str, Any]:
        snapshot = await client.get_memory(challenge_id)
        candidates = None
        if policy.includes("guessing"):
            result = await client.guess(
                challenge_id,
                count=policy.guess_count,
                seed=policy.guess_seed,
            )
            candidates = result.get("candidates")
        return _text(render_board(snapshot, policy, guess_candidates=candidates))

    @tool(  # type: ignore[untyped-decorator]
        "memory_query",
        "用自然語言問白板上已經確認了什麼。回答只會根據白板現況，"
        "不會憑空推論。" + READ_ONLY,
        {"question": Annotated[str, "要問的問題"]},
    )
    async def memory_query(args: dict[str, Any]) -> dict[str, Any]:
        question = str(args.get("question") or "").strip()
        if not question:
            return _text("要問什麼？question 不能是空的。")
        result = await client.query(
            challenge_id,
            question,
            layers=[layer for layer in policy.layers if layer != "guessing"],
        )
        answer = result.get("answer", "")
        references = result.get("references") or []
        if references:
            answer += f"\n\n（依據：{', '.join(references)}）"
        return _text(answer)

    @tool(  # type: ignore[untyped-decorator]
        "memory_guess",
        "卡住的時候用。從這個題型的技術清單裡抽幾個還沒試過的方向。"
        "這些是**建議**不是事實，要自己驗證。" + READ_ONLY,
        {"count": Annotated[int, "要抽幾個，預設 3"]},
    )
    async def memory_guess(args: dict[str, Any]) -> dict[str, Any]:
        count = int(args.get("count") or policy.guess_count)
        result = await client.guess(
            challenge_id, count=max(1, min(count, 10)), seed=policy.guess_seed
        )
        candidates = result.get("candidates") or []
        if not candidates:
            return _text("這個題型的清單裡已經沒有沒試過的方向了。")
        lines = [f"從 {result.get('catalog')} 抽出的方向（尚未驗證）："]
        lines += [f"  {c.get('id')}  {c.get('title', '')}".rstrip() for c in candidates]
        return _text("\n".join(lines))

    return create_sdk_mcp_server(
        name=SERVER_NAME,
        version="1.0.0",
        tools=[memory_show, memory_query, memory_guess],
    )


def describe_tools() -> str:
    """給除錯用的一行摘要。"""
    return json.dumps(TOOL_NAMES, ensure_ascii=False)
