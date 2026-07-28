"""Codex JSONL event normalization.

The Codex adapter consumes ``codex exec --json`` output.  The exact provider event stream
is intentionally kept outside Memory Core; this module projects the useful parts into the
same provider-neutral ``NormalizedRun`` shape used by the Claude adapter.

The mapper is conservative.  If a Codex row does not prove that a command or tool actually
executed, the resulting tool outcome is ``unknown`` rather than evidence-grade success.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from agent_bridge.normalize import AgentRunEvent, NormalizedRun

JsonDict = dict[str, Any]


def normalize_codex(rows: Iterable[JsonDict]) -> NormalizedRun:
    """Project Codex JSONL rows into ``NormalizedRun``.

    Supported rows follow the documented ``codex exec --json`` stream shape: lifecycle
    rows such as ``thread.started`` and item rows such as ``item.started`` /
    ``item.completed``.  The code accepts a few adjacent field spellings because Codex CLI
    event payloads are still a provider boundary, not this repository's frozen contract.
    """

    events: list[AgentRunEvent] = []
    run_meta: dict[str, Any] = {"provider": "openai-codex"}
    started_tools: dict[str, AgentRunEvent] = {}
    skipped: dict[str, int] = {}
    seq = 0

    def add(**kwargs: Any) -> AgentRunEvent:
        nonlocal seq
        seq += 1
        event = AgentRunEvent(seq=seq, **kwargs)
        events.append(event)
        return event

    for row in rows:
        row_type = str(row.get("type") or "")

        if row_type == "thread.started":
            thread_id = row.get("thread_id") or row.get("threadId")
            if isinstance(thread_id, str):
                run_meta["session_id"] = thread_id
            continue

        if row_type == "turn.completed":
            usage = row.get("usage")
            if isinstance(usage, dict):
                run_meta["usage"] = usage
            continue

        if row_type == "turn.failed":
            run_meta["failed"] = True
            add(role="system", kind="notice", content=_message(row, "Codex turn failed."))
            continue

        if row_type == "error":
            run_meta["failed"] = True
            add(role="system", kind="notice", content=_message(row, "Codex emitted an error."))
            continue

        if row_type not in {"item.started", "item.completed"}:
            skipped[row_type or "unknown"] = skipped.get(row_type or "unknown", 0) + 1
            continue

        item = row.get("item")
        if not isinstance(item, dict):
            skipped[f"{row_type}:missing_item"] = skipped.get(f"{row_type}:missing_item", 0) + 1
            continue

        item_type = str(item.get("type") or "")
        item_id = str(item.get("id") or "")

        if item_type in {"agent_message", "message"}:
            text = _text(item)
            if text:
                add(role="assistant", kind="message", content=text)
            continue

        if item_type in {"reasoning", "plan_update", "file_change"}:
            skipped[item_type] = skipped.get(item_type, 0) + 1
            continue

        if _is_tool_like(item_type):
            if row_type == "item.started":
                call = add(
                    role="assistant",
                    kind="tool_call",
                    name=_tool_name(item),
                    tool_use_id=item_id or None,
                    content=_tool_call_content(item),
                    input=_tool_input(item),
                )
                if item_id:
                    started_tools[item_id] = call
                continue

            content = _tool_result_content(item)
            outcome = _outcome(item)
            if item_id and item_id not in started_tools:
                add(
                    role="assistant",
                    kind="tool_call",
                    name=_tool_name(item),
                    tool_use_id=item_id,
                    content=_tool_call_content(item),
                    input=_tool_input(item),
                )
            add(
                role="tool",
                kind="tool_result",
                name=_tool_name(item),
                tool_use_id=item_id or None,
                content=content,
                outcome=outcome,
                is_error=outcome == "failed" or None,
            )
            continue

        skipped[item_type or "unknown_item"] = skipped.get(item_type or "unknown_item", 0) + 1

    if skipped:
        run_meta["skipped"] = skipped
    run_meta["tool_calls"] = sum(1 for event in events if event.kind == "tool_call")
    if any(event.outcome == "interrupted" for event in events):
        run_meta["interrupted"] = True
    return NormalizedRun(events=events, run_meta=run_meta)


def _is_tool_like(item_type: str) -> bool:
    return item_type in {
        "command_execution",
        "mcp_tool_call",
        "tool_call",
        "web_search",
    }


def _tool_name(item: JsonDict) -> str:
    item_type = str(item.get("type") or "tool")
    if item_type == "command_execution":
        return "command_execution"
    return str(item.get("name") or item.get("tool_name") or item_type)


def _tool_input(item: JsonDict) -> dict[str, Any]:
    value = item.get("input")
    if isinstance(value, dict):
        return value
    command = item.get("command")
    if command is not None:
        return {"command": command}
    arguments = item.get("arguments")
    if isinstance(arguments, dict):
        return arguments
    return {}


def _tool_call_content(item: JsonDict) -> str:
    command = item.get("command")
    if isinstance(command, list):
        return " ".join(str(part) for part in command)
    if command is not None:
        return str(command)
    name = _tool_name(item)
    payload = _tool_input(item)
    return f"{name}: {payload}" if payload else name


def _tool_result_content(item: JsonDict) -> str:
    parts: list[str] = []
    for key in ("stdout", "stderr", "output", "text", "result"):
        value = item.get(key)
        if value:
            parts.append(str(value))
    if parts:
        return "\n".join(parts)
    status = item.get("status")
    return f"Codex tool completed with status {status}." if status else "Codex tool completed."


def _outcome(item: JsonDict) -> str:
    status = str(item.get("status") or "").lower()
    if status in {"denied", "blocked", "rejected", "requires_approval"}:
        return "denied"
    if status in {"interrupted", "cancelled", "canceled"}:
        return "interrupted"
    if status in {"failed", "error"}:
        return "failed"

    exit_code = item.get("exit_code", item.get("exitCode"))
    if isinstance(exit_code, int):
        return "succeeded" if exit_code == 0 else "failed"

    if status in {"completed", "succeeded", "success"}:
        return "succeeded"

    return "unknown"


def _text(item: JsonDict) -> str:
    text = item.get("text")
    if isinstance(text, str):
        return text.strip()
    content = item.get("content")
    if isinstance(content, str):
        return content.strip()
    return ""


def _message(row: JsonDict, fallback: str) -> str:
    for key in ("message", "error", "detail"):
        value = row.get(key)
        if value:
            return str(value)
    return fallback
