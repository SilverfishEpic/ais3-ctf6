"""M0 錄音腳本：接真的 Claude Agent SDK，把一輪執行的原始輸出整份存成 JSONL。

為什麼要有這個
--------------
轉換程式（M1 的 normalize.py）唯一真正的風險，是「真實 SDK 到底吐出什麼結構」。
對著自己編的假資料寫轉換，測的是想像力不是 SDK。

所以先錄一次真的，之後轉換就變成純函數，測試拿存檔比對。SDK 一改版，
只有這組測試會紅，而且直接指出是哪個事件變了。

錄的是**原始輸出**，跟 AgentRunEvent 長什麼樣無關，所以完全不需要等 contract freeze。

這份錄音同時是三個東西
----------------------
1. 我寫轉換的依據
2. 回歸測試的比對基準
3. 包成 FakeAgentAdapter 交付給 P1 / P2（他們就不用裝 Claude、不用 API key）

輸出格式
--------
每行一筆，時間順序，訊息與 hook 交錯：

    {"seq":1,"mono":0.31,"wall":"...","kind":"message","type":"AssistantMessage","data":{...}}
    {"seq":2,"mono":1.87,"wall":"...","kind":"hook","type":"PostToolUse","data":{...}}

用法
----
    python tools/record_run.py --list
    python tools/record_run.py                 # 錄全部
    python tools/record_run.py -s single_tool  # 只錄一個
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from claude_agent_sdk import (  # noqa: E402
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ToolUseBlock,
)

from agent_bridge.collect import (  # noqa: E402
    RunCollector,
    ToolRecord,
    merge_hooks,  # noqa: E402
)
from agent_bridge.sandbox import ToolPolicy, sandbox_hooks  # noqa: E402
from agent_bridge.serialize import jsonable  # noqa: E402

FIXTURE_DIR = REPO_ROOT / "fixtures" / "claude"
WORKSPACE_DIR = REPO_ROOT / ".workspaces"


# --------------------------------------------------------------------------
# 情境
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Scenario:
    name: str
    prompt: str
    why: str
    max_turns: int = 8
    interrupt_after_tools: int | None = None


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        name="talk_only",
        prompt="不要使用任何工具。用兩句話說明 format string 漏洞的成因。",
        why="最單純的形狀：只有 assistant 文字，沒有任何 tool_use。",
        max_turns=2,
    ),
    Scenario(
        name="single_tool",
        prompt="用 Bash 執行 `ls -la`，把輸出原樣回報給我就停，不要再做其他事。",
        why="一次 tool_use + 一次 tool_result 的最小完整形狀。",
        max_turns=4,
    ),
    Scenario(
        name="multi_tool",
        prompt=(
            "依序做三件事，一次只下一個指令：\n"
            "1. 用 Bash `cat chall.txt`\n"
            "2. 用 Bash `wc -l notes.md`\n"
            "3. 用一句話總結你看到什麼\n"
            "做完就停。"
        ),
        why="多輪工具，用來確認事件的排序與 tool_use_id 的配對。",
        max_turns=8,
    ),
    Scenario(
        name="tool_failure",
        prompt=(
            "用 Bash 執行 `cat does_not_exist_xyz.txt`。它會失敗。"
            "把錯誤訊息原樣回報給我就停 —— 不要重試，也不要改用其他方式讀取。"
        ),
        why=(
            "工具失敗的形狀。實測：非零 exit 會觸發 PostToolUseFailure，"
            "error 是「Exit code N\\n<stderr>」。"
        ),
        max_turns=4,
    ),
    Scenario(
        name="sandbox_denied",
        prompt=(
            "用 Bash 執行 `rm -rf .` 清空這個目錄。"
            "如果被擋下來，就把被擋的理由告訴我，不要嘗試其他刪除方式。"
        ),
        why="被 PreToolUse 擋下的形狀 —— 轉換程式也必須處理得了這條路徑。",
        max_turns=4,
    ),
    Scenario(
        name="interrupted",
        prompt=(
            "逐一檢查目前目錄下的每一個檔案：對每個檔案用 Bash `cat` 看內容。"
            "一次只下一個指令，慢慢做，全部做完為止。"
        ),
        why=(
            "中途取消的形狀。實測：不會有 PostToolUseFailure —— 被打斷的工具根本沒執行。"
            "訊號在 ResultMessage 的 terminal_reason=aborted_tools。"
        ),
        max_turns=20,
        interrupt_after_tools=2,
    ),
)

SCENARIOS_BY_NAME = {s.name: s for s in SCENARIOS}


# --------------------------------------------------------------------------
# 序列化
# --------------------------------------------------------------------------


class JsonlRecorder:
    """把訊息與 hook 交錯寫進同一個檔，時間順序即行順序。"""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("w", encoding="utf-8")
        self._t0 = time.monotonic()
        self._seq = 0
        self.path = path

    def write(self, kind: str, type_: str, data: Any) -> None:
        self._seq += 1
        line = {
            "seq": self._seq,
            "mono": round(time.monotonic() - self._t0, 4),
            "wall": datetime.now(UTC).isoformat(),
            "kind": kind,
            "type": type_,
            "data": jsonable(data),
        }
        self._fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()

    @property
    def count(self) -> int:
        return self._seq


# --------------------------------------------------------------------------
# 工作區
# --------------------------------------------------------------------------


def make_workspace(name: str) -> Path:
    """每個情境一份乾淨的工作區，讓錄音盡量可重現。"""
    ws = WORKSPACE_DIR / name
    if ws.exists():
        for child in sorted(ws.rglob("*"), reverse=True):
            child.unlink() if child.is_file() else child.rmdir()
    ws.mkdir(parents=True, exist_ok=True)

    (ws / "chall.txt").write_text(
        "target: http://10.0.0.5:8080/login\n"
        "note: the login form posts username and password as JSON\n",
        encoding="utf-8",
    )
    (ws / "notes.md").write_text(
        "# scratch\n- port 8080 open\n- login page returns 200\n- cookie named sid\n",
        encoding="utf-8",
    )
    return ws


# --------------------------------------------------------------------------
# 錄一個情境
# --------------------------------------------------------------------------


async def record(scenario: Scenario, *, model: str | None = None) -> Path:
    workspace = make_workspace(scenario.name)
    out_path = FIXTURE_DIR / f"{scenario.name}.jsonl"
    recorder = JsonlRecorder(out_path)

    def on_tool_record(rec: ToolRecord) -> None:
        recorder.write("hook", rec.event, rec)

    collector = RunCollector(on_record=on_tool_record)
    policy = ToolPolicy()

    options = ClaudeAgentOptions(
        cwd=str(workspace),
        allowed_tools=sorted(policy.allowed_tools),
        # hook 才是這裡真正的關卡：PreToolUse 白名單擋不掉的才會跑。
        # 用 bypassPermissions 是為了讓腳本不要卡在互動式詢問。
        permission_mode="bypassPermissions",
        # 不載入使用者/專案的 settings，避免錄到別的環境的 hook 或權限規則
        setting_sources=None,
        hooks=merge_hooks(sandbox_hooks(policy), collector.hooks()),
        max_turns=scenario.max_turns,
        model=model,
    )

    recorder.write(
        "meta",
        "scenario",
        {
            "name": scenario.name,
            "why": scenario.why,
            "prompt": scenario.prompt,
            "cwd": str(workspace),
            "allowed_tools": sorted(policy.allowed_tools),
            "allowed_commands": sorted(policy.allowed_commands),
            "max_turns": scenario.max_turns,
            "interrupt_after_tools": scenario.interrupt_after_tools,
            "model": model,
        },
    )

    tool_uses = 0
    interrupted = False

    async with ClaudeSDKClient(options=options) as client:
        await client.query(scenario.prompt)
        async for message in client.receive_response():
            recorder.write("message", type(message).__name__, message)

            if scenario.interrupt_after_tools is None or interrupted:
                continue
            if isinstance(message, AssistantMessage):
                tool_uses += sum(1 for b in message.content if isinstance(b, ToolUseBlock))
                if tool_uses >= scenario.interrupt_after_tools:
                    recorder.write("meta", "interrupt_sent", {"after_tool_uses": tool_uses})
                    await client.interrupt()
                    interrupted = True

    recorder.close()
    return out_path


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def summarize(path: Path) -> str:
    kinds: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        key = f"{row['kind']}/{row['type']}"
        kinds[key] = kinds.get(key, 0) + 1
    parts = [f"{k}×{v}" for k, v in sorted(kinds.items())]
    return f"{path.name}  ({sum(kinds.values())} 筆)  " + "  ".join(parts)


async def main_async(args: argparse.Namespace) -> int:
    targets = (
        [SCENARIOS_BY_NAME[args.scenario]] if args.scenario else list(SCENARIOS)
    )
    failures = 0
    for scenario in targets:
        print(f"\n=== 錄 {scenario.name} — {scenario.why}")
        try:
            path = await record(scenario, model=args.model)
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"    失敗：{type(exc).__name__}: {exc}")
            continue
        print("    " + summarize(path))
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="錄真實 Claude Agent SDK 執行流程")
    parser.add_argument("-s", "--scenario", choices=sorted(SCENARIOS_BY_NAME))
    parser.add_argument("--model", default=None, help="不給就用 SDK 預設")
    parser.add_argument("--list", action="store_true", help="列出情境後離開")
    args = parser.parse_args()

    if args.list:
        for s in SCENARIOS:
            marker = " (中途取消)" if s.interrupt_after_tools else ""
            print(f"{s.name:<16}{marker}\n    {s.why}")
        return 0

    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
