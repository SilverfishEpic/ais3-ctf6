"""組 `IngestEnvelope`（spec/openapi.yaml，origin/memory-system）。

Claude 專屬的東西到這裡為止 —— Memory Core 收到的已經是 vendor-neutral 的形狀。

內部模型與線上格式是兩回事
--------------------------
`NormalizedRun` 保留完整資訊（`duration_ms`、`truncated`、`tool_use_id` …），
因為 loop 的停損判斷與除錯都要用。

但 contract 凍結的 `AgentRunEvent` 是 `additionalProperties: false`，只准六個欄位：

    seq, role(assistant|tool|user|system), kind(message|tool_call|tool_result),
    name, content, outcome

所以送出前要**投影**成窄格式。多帶任何未定義欄位，Memory Core 會 422 退回。
`RunLogInput` 同樣是 `additionalProperties: false`，只准 `type` + `events`，
所以 `run_meta` 在線上沒有位置，只留在本地當 loop telemetry。

沒執行過的工具不是證據
----------------------
Memory Core 的 R2 規則（protocol.md §3）現在看 vendor-neutral `outcome`：
`succeeded` / `failed` 是已執行觀察；`denied` / `interrupted` / `unknown`
不能取得 Fact-grade evidence。Bridge 必須保留這個欄位，而不是把事件藏成
system message，否則 contract consumer 無法知道工具結果的真實執行狀態。
"""

from __future__ import annotations

from typing import Any

from agent_bridge.normalize import AgentRunEvent, NormalizedRun

# contract 允許的值（spec/openapi.yaml → AgentRunEvent）
WIRE_FIELDS = ("seq", "role", "kind", "name", "content", "outcome")
WIRE_ROLES = frozenset({"assistant", "tool", "user", "system"})
WIRE_KINDS = frozenset({"message", "tool_call", "tool_result"})
WIRE_OUTCOMES = frozenset({"succeeded", "failed", "denied", "interrupted", "unknown"})


def to_wire_events(events: list[AgentRunEvent]) -> list[dict[str, Any]]:
    """把內部事件投影成 contract 允許的窄格式。"""
    wire: list[dict[str, Any]] = []

    for event in events:
        role, kind, content = event.role, event.kind, event.content

        if kind == "notice":
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
        if kind == "tool_result":
            outcome = event.outcome or "unknown"
            assert outcome in WIRE_OUTCOMES, f"outcome {outcome!r} 不在 contract 的 enum 裡"
            row["outcome"] = outcome
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
