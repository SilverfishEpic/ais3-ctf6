"""Agent Bridge —— CTF 共享記憶系統 v2 的外部 Agent 橋接層（Person 3）。

這一層只做三件事：翻譯、餵料、編排。
`state.yaml` 由 Memory Core 獨佔寫入，這裡一個字都不寫。

翻譯（Claude → Memory Core）::

    Claude 執行
        ↓  PostToolUse hook（collect.RunCollector）+ message stream
    rows（normalize.rows_from_live / load_rows）
        ↓  normalize.normalize
    AgentRunEvent[] + run_meta          ← 內部模型，保留 outcome/duration 等
        ↓  envelope.build_envelope       ← 投影成 contract 允許的窄格式
    IngestEnvelope  →  POST /v1/challenges/{id}/ingest

餵料（Memory Core → Claude）::

    GET /memory  →  inject.render_board / render_diff  →  prompt
    第一輪整份，same-session 之後只給 diff

編排::

    loop.AgentLoop —— normal 一輪 / YOLO 多輪 / sentinel 中止 / 空 diff 停損
                      / 拿到 flag 就停（completion.CompletionDetector，預設關閉）
"""

from agent_bridge.board import is_empty_diff
from agent_bridge.collect import RunCollector, ToolRecord, merge_hooks
from agent_bridge.completion import (
    Baseline,
    BaselineLimits,
    CompletionDetector,
    FlagScan,
    FlagSighting,
    FlagStopConfig,
    StopClass,
    StopVerdict,
    build_flag_pattern,
    scan_events,
    scan_roots,
    scan_workspace,
)
from agent_bridge.envelope import build_envelope, to_wire_events
from agent_bridge.errors import BridgeError, MemoryCoreError
from agent_bridge.inject import render_board, render_diff
from agent_bridge.loop import AgentLoop, LoopLimits, LoopOutcome, StopSignal, TurnRecord
from agent_bridge.normalize import (
    AgentRunEvent,
    NormalizedRun,
    load_rows,
    normalize,
    rows_from_live,
)
from agent_bridge.policy import (
    ABLATION_LEVELS,
    FACT_AND_ASSUMPTION,
    FACT_ONLY,
    FULL,
    NO_MEMORY,
    MemoryPolicy,
    SessionMode,
)
from agent_bridge.protocols import AgentAdapter, MemoryClient, RunResult
from agent_bridge.sandbox import (
    DEFAULT_TOOLS,
    READ_ONLY_TOOLS,
    RECON_COMMANDS,
    ToolPolicy,
    make_sandbox_hook,
    sandbox_hooks,
)
from agent_bridge.serialize import jsonable
from agent_bridge.truncate import TruncationPolicy, truncate_text

__all__ = [
    "ABLATION_LEVELS",
    "DEFAULT_TOOLS",
    "FACT_AND_ASSUMPTION",
    "FACT_ONLY",
    "FULL",
    "NO_MEMORY",
    "READ_ONLY_TOOLS",
    "RECON_COMMANDS",
    "AgentAdapter",
    "AgentLoop",
    "AgentRunEvent",
    "Baseline",
    "BaselineLimits",
    "BridgeError",
    "CompletionDetector",
    "FlagScan",
    "FlagSighting",
    "FlagStopConfig",
    "LoopLimits",
    "LoopOutcome",
    "MemoryClient",
    "MemoryCoreError",
    "MemoryPolicy",
    "NormalizedRun",
    "RunCollector",
    "RunResult",
    "SessionMode",
    "StopClass",
    "StopSignal",
    "StopVerdict",
    "ToolPolicy",
    "ToolRecord",
    "TruncationPolicy",
    "TurnRecord",
    "build_envelope",
    "build_flag_pattern",
    "is_empty_diff",
    "jsonable",
    "load_rows",
    "make_sandbox_hook",
    "merge_hooks",
    "normalize",
    "render_board",
    "render_diff",
    "rows_from_live",
    "sandbox_hooks",
    "scan_events",
    "scan_roots",
    "scan_workspace",
    "to_wire_events",
    "truncate_text",
]
