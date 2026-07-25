"""把一輪 Claude 執行轉成 vendor-neutral 的 `AgentRunEvent[]`。

依規劃書 §8.2 的形狀，加上三個依實測提出的欄位（見下方「給 P1 的提案」）。

輸入是「錄音列」—— `{"kind": ..., "type": ..., "data": ...}` 的序列。
正式執行與重播錄音都組成同一種列，所以 `fixtures/claude/*.jsonl` 測到的
就是生產路徑，不是另一條平行的程式碼。

實際的訊息形狀請看 `docs/agent-bridge/m0-findings.md`，那份是對真 SDK 錄出來的。

給 P1 的提案（contract 未定案的部分）
------------------------------------
1. `truncated` / `original_bytes`（第 2 題）—— 沒有這兩個欄位，journal 這份
   「原始證據紀錄」在內容被砍過時會說謊。
2. `input` 結構化工具參數（第 4 題）—— §8.2 範例把 `checksec ./chall` 塞進
   `content` 字串，但 Bash 以外的工具參數是 dict，字串塞不下。這裡兩個都給：
   `content` 保持人類可讀，`input` 是結構化的真相。
3. `outcome` —— 實測有五種結局，對 Summarizer 意義完全不同：
   `succeeded` / `failed`（工具有執行，是觀察證據）/ `denied`（被沙箱擋下，
   **不是**關於題目的任何證據）/ `interrupted`（人喊停，什麼都不能推論）/
   `unknown`（無足夠 provenance）。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from agent_bridge.collect import ToolRecord
from agent_bridge.serialize import jsonable
from agent_bridge.truncate import TruncationPolicy, apply_run_budget

JsonDict = dict[str, Any]

# 中斷時 SDK 塞進 tool_result 的固定字串，見 m0-findings.md §5.3
INTERRUPT_MARKERS = (
    "The user doesn't want to proceed with this tool use",
    "[Request interrupted by user",
)

# 不帶證據價值、不進 events 的訊息型別（仍會計入 run_meta.skipped）
SKIPPED_MESSAGE_TYPES = frozenset({"SystemMessage", "RateLimitEvent"})


@dataclass
class AgentRunEvent:
    seq: int
    role: str  # assistant | tool | system
    kind: str  # message | tool_call | tool_result | notice
    content: str
    name: str | None = None
    tool_use_id: str | None = None
    input: dict[str, Any] | None = None
    outcome: str | None = None
    is_error: bool | None = None
    truncated: bool = False
    original_bytes: int | None = None
    duration_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None and v is not False}


@dataclass
class NormalizedRun:
    events: list[AgentRunEvent] = field(default_factory=list)
    run_meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "events": [e.to_dict() for e in self.events],
            "run_meta": self.run_meta,
        }


# ---------------------------------------------------------------------------
# 組「錄音列」
# ---------------------------------------------------------------------------


def rows_from_live(messages: Sequence[Any], tool_records: Sequence[ToolRecord]) -> list[JsonDict]:
    """正式執行用：把 SDK 訊息與 hook 紀錄組成跟錄音檔一樣的列。"""
    rows: list[JsonDict] = [
        {"kind": "message", "type": type(m).__name__, "data": jsonable(m)} for m in messages
    ]
    rows += [
        {"kind": "hook", "type": r.event, "data": jsonable(r)} for r in tool_records
    ]
    return rows


def load_rows(path: str | Path) -> list[JsonDict]:
    """重播用：讀一份錄音檔。"""
    text = Path(path).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# 轉換
# ---------------------------------------------------------------------------


def normalize(
    rows: Iterable[JsonDict],
    *,
    policy: TruncationPolicy | None = None,
) -> NormalizedRun:
    policy = policy or TruncationPolicy()
    rows = list(rows)

    hooks_by_id: dict[str, JsonDict] = {}
    result_data: dict[str, Any] | None = None
    scenario: dict[str, Any] | None = None
    skipped: dict[str, int] = {}

    # 第一遍：建索引。要先知道 permission_denials 才判斷得出 denied。
    for row in rows:
        if row["kind"] == "hook":
            tid = row["data"].get("tool_use_id")
            if tid:
                hooks_by_id[tid] = row["data"]
        elif row["kind"] == "meta" and row["type"] == "scenario":
            scenario = row["data"]
        elif row["kind"] == "message" and row["type"] == "ResultMessage":
            result_data = row["data"]

    denied_ids = {
        d.get("tool_use_id")
        for d in (result_data or {}).get("permission_denials") or []
        if isinstance(d, dict)
    }

    # 第二遍：照順序產生事件
    events: list[AgentRunEvent] = []
    thinking_blocks = 0
    seq = 0
    # ResultMessage 沒有 model，只有 AssistantMessage 有 —— 但 §8.2 的
    # source.model 是必要欄位，所以從這裡撈。
    model: str | None = None

    def add(**kwargs: Any) -> AgentRunEvent:
        nonlocal seq
        seq += 1
        event = AgentRunEvent(seq=seq, **kwargs)
        events.append(event)
        return event

    for row in rows:
        if row["kind"] != "message":
            continue
        mtype = row["type"]
        if mtype in SKIPPED_MESSAGE_TYPES:
            skipped[mtype] = skipped.get(mtype, 0) + 1
            continue

        data = row["data"]

        if mtype == "AssistantMessage":
            model = model or data.get("model")
            for block in data.get("content") or []:
                btype = block.get("__type__")
                if btype == "ThinkingBlock":
                    # 內容永遠是空的（m0-findings §2），不製造噪音，只計數
                    thinking_blocks += 1
                elif btype == "TextBlock":
                    text = (block.get("text") or "").strip()
                    if text:
                        add(role="assistant", kind="message", content=text)
                elif btype == "ToolUseBlock":
                    tool_input = block.get("input") or {}
                    add(
                        role="assistant",
                        kind="tool_call",
                        name=block.get("name"),
                        tool_use_id=block.get("id"),
                        content=_render_tool_call(block.get("name"), tool_input),
                        input=tool_input,
                    )
                else:
                    skipped[btype or "UnknownBlock"] = skipped.get(btype or "UnknownBlock", 0) + 1

        elif mtype == "UserMessage":
            content = data.get("content")
            blocks = content if isinstance(content, list) else []
            for block in blocks:
                btype = block.get("__type__")
                if btype == "ToolResultBlock":
                    _add_tool_result(add, block, hooks_by_id, denied_ids)
                elif btype == "TextBlock":
                    text = (block.get("text") or "").strip()
                    if text:
                        # 例如「[Request interrupted by user for tool use]」——
                        # 這是控制訊息，不是使用者說的話
                        add(role="system", kind="notice", content=text)
                else:
                    skipped[btype or "UnknownBlock"] = skipped.get(btype or "UnknownBlock", 0) + 1

        elif mtype == "ResultMessage":
            continue  # 進 run_meta，不是事件

        else:
            skipped[mtype] = skipped.get(mtype, 0) + 1

    _apply_truncation(events, policy)

    return NormalizedRun(
        events=events,
        run_meta=_build_run_meta(
            result_data, scenario, thinking_blocks, skipped, hooks_by_id, events, model
        ),
    )


# ---------------------------------------------------------------------------
# 內部
# ---------------------------------------------------------------------------


def _render_tool_call(name: str | None, tool_input: dict[str, Any]) -> str:
    """人類可讀的一行。結構化的真相在 `input` 欄位。"""
    if name == "Bash" and "command" in tool_input:
        return str(tool_input["command"])
    for key in ("file_path", "pattern", "url", "path", "query"):
        if key in tool_input:
            return f"{name}: {tool_input[key]}"
    return f"{name}: " + json.dumps(tool_input, ensure_ascii=False, sort_keys=True)


def _render_tool_output(hook: JsonDict | None, stream_content: Any) -> str:
    """優先用 hook 的結構化輸出 —— 它比 stream 壓平過的字串完整（m0-findings §4）。"""
    if hook is not None:
        if hook.get("error") is not None:
            return str(hook["error"])
        response = hook.get("tool_response")
        if isinstance(response, dict):
            parts = [
                str(response[key])
                for key in ("stdout", "stderr")
                if response.get(key)
            ]
            if parts:
                return "\n".join(parts)
            return json.dumps(response, ensure_ascii=False, sort_keys=True)
        if response is not None:
            return str(response)

    if isinstance(stream_content, str):
        return stream_content
    if isinstance(stream_content, list):
        return "\n".join(
            str(part.get("text", part)) if isinstance(part, dict) else str(part)
            for part in stream_content
        )
    return "" if stream_content is None else str(stream_content)


def _classify(
    hook: JsonDict | None,
    is_error: bool,
    text: str,
    tool_use_id: str,
    denied: set[str | None],
) -> str:
    """四種結局對 Summarizer 的意義完全不同，見模組開頭的提案 3。"""
    if tool_use_id in denied:
        return "denied"
    if hook is not None:
        if hook.get("is_interrupt"):
            return "interrupted"
        return "failed" if hook.get("event") == "PostToolUseFailure" else "succeeded"
    # 沒有 hook 紀錄 = 指令根本沒執行
    if any(marker in text for marker in INTERRUPT_MARKERS):
        return "interrupted"
    return "unknown"


def _add_tool_result(
    add: Callable[..., AgentRunEvent],
    block: JsonDict,
    hooks_by_id: dict[str, JsonDict],
    denied: set[str | None],
) -> None:
    tool_use_id = block.get("tool_use_id") or ""
    hook = hooks_by_id.get(tool_use_id)
    is_error = bool(block.get("is_error"))
    text = _render_tool_output(hook, block.get("content"))
    add(
        role="tool",
        kind="tool_result",
        name=(hook or {}).get("tool_name"),
        tool_use_id=tool_use_id,
        content=text,
        outcome=_classify(hook, is_error, text, tool_use_id, denied),
        is_error=is_error or None,
        duration_ms=(hook or {}).get("duration_ms"),
    )


def _apply_truncation(events: list[AgentRunEvent], policy: TruncationPolicy) -> None:
    """只截斷工具輸出 —— assistant 講的話本來就不長，砍了反而失真。"""
    targets = [e for e in events if e.kind == "tool_result"]
    if not targets:
        return
    for event, result in zip(
        targets,
        apply_run_budget([e.content for e in targets], policy),
        strict=True,
    ):
        event.content = result.text
        event.truncated = result.truncated
        event.original_bytes = result.original_bytes


def _build_run_meta(
    result: dict[str, Any] | None,
    scenario: dict[str, Any] | None,
    thinking_blocks: int,
    skipped: dict[str, int],
    hooks_by_id: dict[str, JsonDict],
    events: list[AgentRunEvent],
    model: str | None,
) -> dict[str, Any]:
    """這些不是證據，所以不進 events[]（contract 第 3 題的具體提案）。"""
    meta: dict[str, Any] = {
        "model": model,
        "thinking_blocks": thinking_blocks,
        "tool_calls": len(hooks_by_id),
        "skipped": skipped,
    }
    if scenario:
        meta["scenario"] = scenario.get("name")
    if result:
        meta.update(
            {
                "subtype": result.get("subtype"),
                "is_error": result.get("is_error"),
                "stop_reason": result.get("stop_reason"),
                "terminal_reason": result.get("terminal_reason"),
                "num_turns": result.get("num_turns"),
                "duration_ms": result.get("duration_ms"),
                "duration_api_ms": result.get("duration_api_ms"),
                "total_cost_usd": result.get("total_cost_usd"),
                "session_id": result.get("session_id"),
                "usage": result.get("usage"),
            }
        )
    # 中斷判斷用兩個訊號互補（m0-findings §5.3）：
    # terminal_reason 會隨中斷落在哪個階段而不同（aborted_tools / aborted_streaming），
    # 所以不能寫死其中一個；同時看事件層有沒有被打斷的工具，兩邊有一個成立就算。
    terminal = str((result or {}).get("terminal_reason") or "")
    meta["interrupted"] = terminal.startswith("aborted") or any(
        e.outcome == "interrupted" for e in events
    )
    return {k: v for k, v in meta.items() if v is not None}
