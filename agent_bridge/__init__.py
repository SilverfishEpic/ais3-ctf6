"""Agent Bridge —— CTF 共享記憶系統 v2 的外部 Agent 橋接層（Person 3）。

這一層只做三件事：翻譯、餵料、編排。
`state.yaml` 由 Memory Core 獨佔寫入，這裡一個字都不寫。
"""

from agent_bridge.collect import RunCollector, ToolRecord, merge_hooks
from agent_bridge.sandbox import (
    DEFAULT_TOOLS,
    READ_ONLY_TOOLS,
    RECON_COMMANDS,
    ToolPolicy,
    make_sandbox_hook,
    sandbox_hooks,
)

__all__ = [
    "DEFAULT_TOOLS",
    "READ_ONLY_TOOLS",
    "RECON_COMMANDS",
    "RunCollector",
    "ToolPolicy",
    "ToolRecord",
    "make_sandbox_hook",
    "merge_hooks",
    "sandbox_hooks",
]
