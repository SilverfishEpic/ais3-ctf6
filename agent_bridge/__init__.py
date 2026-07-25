"""Agent Bridge —— CTF 共享記憶系統 v2 的外部 Agent 橋接層（Person 3）。

這一層只做三件事：翻譯、餵料、編排。
`state.yaml` 由 Memory Core 獨佔寫入，這裡一個字都不寫。

翻譯這條線的完整路徑::

    Claude 執行
        ↓  PostToolUse hook（collect.RunCollector）+ message stream
    rows（normalize.rows_from_live / load_rows）
        ↓  normalize.normalize
    AgentRunEvent[] + run_meta
        ↓  envelope.build_envelope
    IngestEnvelope  →  POST /ingest
"""

from agent_bridge.collect import RunCollector, ToolRecord, merge_hooks
from agent_bridge.envelope import build_envelope
from agent_bridge.normalize import (
    AgentRunEvent,
    NormalizedRun,
    load_rows,
    normalize,
    rows_from_live,
)
from agent_bridge.protocols import AgentAdapter, RunResult
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
    "DEFAULT_TOOLS",
    "READ_ONLY_TOOLS",
    "RECON_COMMANDS",
    "AgentAdapter",
    "AgentRunEvent",
    "NormalizedRun",
    "RunCollector",
    "RunResult",
    "ToolPolicy",
    "ToolRecord",
    "TruncationPolicy",
    "build_envelope",
    "jsonable",
    "load_rows",
    "make_sandbox_hook",
    "merge_hooks",
    "normalize",
    "rows_from_live",
    "sandbox_hooks",
    "truncate_text",
]
