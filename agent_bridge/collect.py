"""每一輪的工具紀錄收集器。

決定 1：hook 只負責「收集」，`/ingest` 由 loop 每輪送一次。
Summarizer 是 550B，每個 `ls` 都觸發一次既貴又會把白板灌滿雜訊。

這一層刻意只做兩件事：**照原樣留下來**，以及**標上真實時間**。
不截斷（那是 truncate.py）、不轉格式（那是 normalize.py），兩者都是 M1。

hook 相對於直接讀 message stream 多給的東西只有兩樣，但都是真的：
1. 真實時間戳 —— 工具跑完的當下記錄，不是整輪結束後回推
2. 串流中斷時，已經觸發過的部分仍在 buffer 裡，不會整輪掉光
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agent_bridge.sdk_compat import HookMatcher


@dataclass
class ToolRecord:
    """一次工具呼叫的完整原始紀錄。內容不做任何裁切。"""

    seq: int
    event: str  # "PostToolUse" | "PostToolUseFailure"
    tool_use_id: str
    tool_name: str
    tool_input: dict[str, Any]
    session_id: str
    finished_at: float  # wall clock, epoch 秒
    tool_response: Any = None
    error: str | None = None
    is_interrupt: bool = False
    duration_ms: float | None = None
    agent_id: str | None = None
    agent_type: str | None = None

    @property
    def ok(self) -> bool:
        return self.event == "PostToolUse"


class RunCollector:
    """掛在 hook 上，累積這一輪的工具紀錄。

    用法::

        collector = RunCollector()
        options = ClaudeAgentOptions(hooks=merge_hooks(
            sandbox_hooks(policy),
            collector.hooks(),
        ))
        ...  # 跑一輪
        records = collector.drain()   # 取出並清空，準備打包送 ingest

    `on_record` 可以拿來即時落地（錄製腳本就是這樣用的）。
    """

    def __init__(
        self,
        on_record: Callable[[ToolRecord], None] | None = None,
        *,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._on_record = on_record
        self._clock = clock
        self._monotonic = monotonic
        self._records: list[ToolRecord] = []
        self._starts: dict[str, float] = {}
        self._seq = 0

    # ---- hook 設定 -------------------------------------------------

    def hooks(self) -> dict[str, list[HookMatcher]]:
        return {
            "PreToolUse": [HookMatcher(hooks=[self._on_pre])],
            "PostToolUse": [HookMatcher(hooks=[self._on_post])],
            "PostToolUseFailure": [HookMatcher(hooks=[self._on_failure])],
        }

    # ---- callbacks -------------------------------------------------

    async def _on_pre(
        self, input_data: dict[str, Any], tool_use_id: str | None, context: Any
    ) -> dict[str, Any]:
        # 只記開始時間。放行與否交給 sandbox 那個 hook，兩個並行跑、deny 優先。
        tid = _tool_use_id(input_data, tool_use_id)
        if tid:
            self._starts[tid] = self._monotonic()
        return {}

    async def _on_post(
        self, input_data: dict[str, Any], tool_use_id: str | None, context: Any
    ) -> dict[str, Any]:
        self._append(
            input_data,
            tool_use_id,
            event="PostToolUse",
            tool_response=input_data.get("tool_response"),
        )
        return {}

    async def _on_failure(
        self, input_data: dict[str, Any], tool_use_id: str | None, context: Any
    ) -> dict[str, Any]:
        self._append(
            input_data,
            tool_use_id,
            event="PostToolUseFailure",
            error=input_data.get("error"),
            is_interrupt=bool(input_data.get("is_interrupt", False)),
        )
        return {}

    # ---- 內部 ------------------------------------------------------

    def _append(
        self,
        input_data: dict[str, Any],
        tool_use_id: str | None,
        *,
        event: str,
        tool_response: Any = None,
        error: str | None = None,
        is_interrupt: bool = False,
    ) -> None:
        tid = _tool_use_id(input_data, tool_use_id)
        started = self._starts.pop(tid, None) if tid else None
        duration_ms = None if started is None else (self._monotonic() - started) * 1000.0

        self._seq += 1
        record = ToolRecord(
            seq=self._seq,
            event=event,
            tool_use_id=tid or "",
            tool_name=input_data.get("tool_name", ""),
            tool_input=input_data.get("tool_input", {}) or {},
            session_id=input_data.get("session_id", ""),
            finished_at=self._clock(),
            tool_response=tool_response,
            error=error,
            is_interrupt=is_interrupt,
            duration_ms=duration_ms,
            agent_id=input_data.get("agent_id"),
            agent_type=input_data.get("agent_type"),
        )
        self._records.append(record)
        if self._on_record is not None:
            self._on_record(record)

    # ---- 取用 ------------------------------------------------------

    def peek(self) -> list[ToolRecord]:
        """看一眼，不清空。"""
        return list(self._records)

    def drain(self) -> list[ToolRecord]:
        """取出這一輪累積的紀錄並清空，準備打包送 ingest。"""
        out, self._records = self._records, []
        return out


def _tool_use_id(input_data: dict[str, Any], fallback: str | None) -> str | None:
    return input_data.get("tool_use_id") or fallback


def merge_hooks(*configs: dict[str, list[HookMatcher]]) -> dict[str, list[HookMatcher]]:
    """把多份 hook 設定合起來。

    同一個事件掛多個 matcher 是允許的 —— 它們並行執行，權限決定上 deny 優先。
    sandbox 與 collector 都要掛 PreToolUse，就是靠這個合併。
    """
    merged: dict[str, list[HookMatcher]] = {}
    for config in configs:
        for event, matchers in config.items():
            merged.setdefault(event, []).extend(matchers)
    return merged
