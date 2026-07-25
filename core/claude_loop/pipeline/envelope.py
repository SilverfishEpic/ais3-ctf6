"""組 `IngestEnvelope`（spec/openapi.yaml，origin/memory-system）。

Claude 專屬的東西到這裡為止 —— Memory Core 收到的已經是 vendor-neutral 的形狀。

內部模型與線上格式是兩回事
--------------------------
`NormalizedRun` 保留完整資訊（`outcome`、`duration_ms`、`truncated`、
`tool_use_id` …），因為 loop 的停損判斷與除錯都要用。

但 contract 凍結的 `AgentRunEvent` 是 `additionalProperties: false`，只准五個欄位：

    seq, role(assistant|tool|user|system), kind(message|tool_call|tool_result),
    name, content

所以送出前要**投影**成窄格式。多帶任何一個欄位，Memory Core 會 422 退回。
`RunLogInput` 同樣是 `additionalProperties: false`，只准 `type` + `events`，
所以 `run_meta` 在線上沒有位置，只留在本地當 loop telemetry。

沒執行過的工具不是證據
----------------------
Memory Core 的 R2 規則（protocol.md §3）判斷「這份 run_log 算不算直接證據」的
實作是：

    any(event.kind == "tool_result" for event in events)

它只看有沒有 `tool_result`，不看那個工具**有沒有真的執行**。

被沙箱擋下、或被人中止的工具呼叫，指令從來沒有跑過，它沒有產生任何 tool result。
如果照樣用 `kind: tool_result` 送出去，Summarizer 就會把「rm 不在白名單」
當成關於這一題的直接證據，寫進白板。

所以投影時把它們降級成 `role: system` + `kind: message`：語意上更誠實，
而且不需要 Memory Core 改任何東西。
"""

from __future__ import annotations

from typing import Any

from core.claude_loop.agent_bridge.normalize import AgentRunEvent, NormalizedRun

# contract 允許的值（spec/openapi.yaml → AgentRunEvent）
WIRE_FIELDS = ("seq", "role", "kind", "name", "content")
WIRE_ROLES = frozenset({"assistant", "tool", "user", "system"})
WIRE_KINDS = frozenset({"message", "tool_call", "tool_result"})

# 沒有真的執行過的結局
NOT_EXECUTED = {
    "denied": "[not executed: blocked by sandbox]",
    "interrupted": "[not executed: interrupted by user]",
}


def to_wire_events(events: list[AgentRunEvent]) -> list[dict[str, Any]]:
    """把內部事件投影成 contract 允許的窄格式。"""
    wire: list[dict[str, Any]] = []

    for event in events:
        role, kind, content = event.role, event.kind, event.content

        if kind == "tool_result" and event.outcome in NOT_EXECUTED:
            # 沒跑過的東西不是 tool_result，見模組開頭
            role, kind = "system", "message"
            marker = NOT_EXECUTED[event.outcome]
            tool = f" {event.name}" if event.name else ""
            content = f"{marker}{tool}: {content}"
        elif kind == "notice":
            # 控制訊息（例如「已中斷」），contract 沒有這個 kind
            role, kind = "system", "message"

        assert role in WIRE_ROLES, f"role {role!r} 不在 contract 的 enum 裡"
        assert kind in WIRE_KINDS, f"kind {kind!r} 不在 contract 的 enum 裡"

        row: dict[str, Any] = {
            "seq": len(wire) + 1,  # 投影後重新編號，保持連續
            "role": role,
            "kind": kind,
            "content": content,
        }
        if event.name is not None:
            row["name"] = event.name
        wire.append(row)

    return wire


def build_envelope(
    run: NormalizedRun,
    *,
    provider: str = "anthropic",
    model: str | None = None,
    run_id: str | None = None,
    session_id: str | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    """把一輪的結果包成 `POST /v1/challenges/{id}/ingest` 的 body。

    只輸出 contract 允許的欄位。`run.run_meta` 留在本地，不上線。
    """
    meta = run.run_meta

    source: dict[str, Any] = {
        "type": "agent",
        "provider": provider,
        "model": model or meta.get("model"),
        "run_id": run_id,
        "session_id": session_id or meta.get("session_id"),
    }
    if actor is not None:
        source["actor"] = actor

    return {
        "source": source,
        "input": {
            "type": "run_log",
            "events": to_wire_events(run.events),
        },
    }
