"""PreToolUse 白名單 —— 由 harness 強制執行，Agent 繞不過。

決定 6：不自寫 jail，改用 Claude Agent SDK 的 `PreToolUse` hook 回 `deny`。
Hook 在工具真的執行之前跑，而且多個 hook 同時掛的時候 deny 優先，
所以這一層不受 Agent 的措辭或 prompt injection 影響。

誠實說明這一層擋得住什麼、擋不住什麼
------------------------------------
擋得住：沒被允許的工具、不在名單內的執行檔、用 shell 串接或包裝程式繞過名單。
這些在工具跑起來之前就被拒絕，並附上理由讓 Agent 知道原因、不要盲目重試。

擋不住：名單裡只要放進 `python`、`nc`、`bash` 這類能執行任意程式碼的東西，
它就從「沙箱」退化成「防手滑的護欄」。真的要跑 CTF 免不了要放這些，所以
M2 接真題目時必須另外決定隔離層（容器 / WSL 分身），不能只靠這個檔案。

M0 預設用最小的唯讀 recon 名單，因為錄音只需要跑得動幾個安全指令。
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, replace
from pathlib import PurePath
from typing import Any

from claude_agent_sdk import HookMatcher

# 指令替換能把任意東西藏進一個看起來乾淨的指令裡（`echo $(rm x)` 的第一個
# token 是 echo），任何逐段分析都攔不住，所以不分模式一律拒絕。
SUBSTITUTION_OPERATORS: tuple[str, ...] = ("`", "$(")

# 串接：預設拒絕；放寬時改成「每一段都要各自過白名單」。
CHAIN_OPERATORS: tuple[str, ...] = ("&&", "||", ";", "|", "&", "\n")

# 重導向：預設拒絕（會寫檔），放寬時允許。
REDIRECTION_OPERATORS: tuple[str, ...] = (">>", ">", "<")

_SEGMENT_SPLIT = re.compile(r"\|\||&&|[;|&\n]")

# 這些程式的用途就是「改變另一個程式怎麼跑」，放行它們等於在名單上開後門。
WRAPPER_COMMANDS: frozenset[str] = frozenset({
    "sudo", "doas", "su", "env", "nohup", "time", "nice", "timeout",
    "xargs", "command", "eval", "exec", "sh", "bash", "zsh", "dash", "fish",
})

# 只讀不寫的內建工具
READ_ONLY_TOOLS: frozenset[str] = frozenset({"Read", "Glob", "Grep"})

# M0 錄音用的預設工具集
DEFAULT_TOOLS: frozenset[str] = READ_ONLY_TOOLS | frozenset({"Bash", "TodoWrite"})

# M0 預設的 Bash 執行檔名單：純觀察，不改動任何東西，也不能執行任意程式碼
RECON_COMMANDS: frozenset[str] = frozenset({
    "ls", "cat", "file", "head", "tail", "wc", "stat", "pwd", "echo", "which",
    "strings", "xxd", "od", "readelf", "objdump", "nm", "checksec",
    "md5sum", "sha256sum", "base64",
})


@dataclass(frozen=True)
class ToolPolicy:
    """一份工具白名單。用 `extend()` 加東西，不要就地改。"""

    allowed_tools: frozenset[str] = DEFAULT_TOOLS
    allowed_commands: frozenset[str] = RECON_COMMANDS
    allow_shell_operators: bool = False

    def extend(
        self,
        *,
        tools: frozenset[str] | set[str] | None = None,
        commands: frozenset[str] | set[str] | None = None,
        allow_shell_operators: bool | None = None,
    ) -> "ToolPolicy":
        return replace(
            self,
            allowed_tools=self.allowed_tools | frozenset(tools or ()),
            allowed_commands=self.allowed_commands | frozenset(commands or ()),
            allow_shell_operators=(
                self.allow_shell_operators
                if allow_shell_operators is None
                else allow_shell_operators
            ),
        )

    def check(self, tool_name: str, tool_input: dict[str, Any]) -> str | None:
        """回 None 代表放行；回字串代表拒絕，字串本身就是給 Agent 看的理由。"""
        if tool_name not in self.allowed_tools:
            allowed = ", ".join(sorted(self.allowed_tools))
            return f"工具 {tool_name} 不在這次執行的白名單內。可用的工具：{allowed}"

        if tool_name != "Bash":
            return None

        command = str(tool_input.get("command", "")).strip()
        if not command:
            return "Bash 指令是空的"

        for op in SUBSTITUTION_OPERATORS:
            if op in command:
                return (
                    f"指令中含有指令替換 {op!r}。它能把任意程式藏進看起來安全的指令裡，"
                    "所以一律不允許。請把要執行的東西直接寫出來。"
                )

        if self.allow_shell_operators:
            segments = [seg.strip() for seg in _SEGMENT_SPLIT.split(command)]
            segments = [seg for seg in segments if seg]
        else:
            for op in CHAIN_OPERATORS + REDIRECTION_OPERATORS:
                if op in command:
                    return (
                        f"指令中含有 shell 運算子 {op!r}，這次執行不允許串接或重導向。"
                        "請改成單一指令分次執行。"
                    )
            segments = [command]

        for segment in segments:
            reason = self._check_segment(segment)
            if reason is not None:
                return reason
        return None

    def _check_segment(self, segment: str) -> str | None:
        exe = _executable(segment)
        if exe is None:
            return f"看不出這串指令要執行什麼：{segment!r}"
        if exe in WRAPPER_COMMANDS:
            return (
                f"{exe} 是包裝／轉譯程式，用它可以繞過指令白名單，所以不允許。"
                "請直接執行目標指令。"
            )
        if exe not in self.allowed_commands:
            allowed = ", ".join(sorted(self.allowed_commands))
            return f"指令 {exe} 不在白名單內。可用的指令：{allowed}"
        return None


def _executable(segment: str) -> str | None:
    """挑出這一段指令真正要執行的程式名稱。

    會剝掉 `FOO=1` 這種環境變數前綴。包裝程式（sudo / env / bash ...）不在這裡
    展開 —— 它們由 `WRAPPER_COMMANDS` 直接拒絕，因為展開就等於承認可以繞過名單。
    """
    try:
        tokens = shlex.split(segment, posix=True)
    except ValueError:
        # 引號沒閉合之類 —— 判斷不了就不要放行
        return None

    for token in tokens:
        head = token.split("=", 1)[0]
        if "=" in token and not token.startswith("-") and "/" not in head:
            continue  # FOO=bar 前綴
        return PurePath(token).name
    return None


def _deny(reason: str) -> dict[str, Any]:
    return {
        # 給人看的
        "systemMessage": f"[sandbox] 已擋下：{reason}",
        # 給模型看的，讓它知道為什麼而不是盲目重試
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
    }


def make_sandbox_hook(policy: ToolPolicy):
    """做出一個可以掛在 PreToolUse 的 callback。"""

    async def sandbox_hook(input_data, tool_use_id, context):  # noqa: ANN001
        if input_data.get("hook_event_name") != "PreToolUse":
            return {}
        reason = policy.check(
            input_data.get("tool_name", ""),
            input_data.get("tool_input", {}) or {},
        )
        return _deny(reason) if reason else {}

    return sandbox_hook


def sandbox_hooks(policy: ToolPolicy | None = None) -> dict[str, list[HookMatcher]]:
    """給 ClaudeAgentOptions.hooks 用的設定片段。"""
    policy = policy or ToolPolicy()
    return {"PreToolUse": [HookMatcher(hooks=[make_sandbox_hook(policy)])]}
