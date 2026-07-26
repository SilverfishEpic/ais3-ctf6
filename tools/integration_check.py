"""起點文件 §10 的第一輪整合測試 —— 打真的 HTTP,不是 in-process。

離線測試只證明每個模組自己前後一致。這支證明**三個人的東西接得起來**:
Memory Core 真的在跑、Agent Bridge 真的送得進去、diff 真的回得來、
optimistic concurrency 真的會擋。

起點文件 §10 定義的 11 步,以及這裡實際怎麼做::

     1  建立 demo-web challenge              create_challenge
     2  Human ingest 一段 nmap 觀察          ingest(source=human, type=note)
     3  Summarizer 產生記憶項目              (Memory Core 自己做)
     4  revision 0 → 1                       斷言
     5  能顯示白板內容                        get_memory + render_board
     6  Agent 讀白板                          AgentLoop 強制注入(決定 3)
     7  Agent 回 normalized run log           FakeAgentAdapter 重播真紀錄
     8  revision 1 → 2                        斷言
     9  Human promote,帶 expected_revision   mutate
    10  stale revision → 409                  mutate(舊的 revision)
    11  guess --seed 42 可重現                guess ×2 比對

關於第 3 步的 Fact
------------------
文件說 Mock Summarizer 要產生 `F001: TCP/80 is open.` 與
`A001: The target may expose an HTTP application.` —— `--fake-summarizer`
對這段 note 的回應**一字不差就是這兩條**,所以第 3 步是真的過,不是放水過。

但斷言仍然只檢查「有 Fact 也有 Assumption」而不是比對字串,因為換成真的 AIS3
Summarizer 之後文字一定會不一樣,那不該讓這支腳本紅。實際文字會印出來。

一個順帶的觀察:Agent 的執行紀錄(第 7 步)帶著兩個 `outcome: succeeded` 的
`tool_result`,在 R2 底下**有資格**支撐 Fact,但 `--fake-summarizer` 只從它生出
Assumption。那是合規的(§6.2:證據不足就維持 Assumption),不是 bug ——
只是提醒:Fact 生不生得出來要看 Summarizer,不是看 bridge。

用法::

    python -m memory_core --fake-summarizer --runs ./runs --templates ./templates --port 8777
    python tools/integration_check.py --base-url http://127.0.0.1:8777
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from agent_bridge.adapters.fake import FakeAgentAdapter  # noqa: E402
from agent_bridge.board import revision  # noqa: E402
from agent_bridge.errors import MemoryCoreError  # noqa: E402
from agent_bridge.inject import render_board  # noqa: E402
from agent_bridge.loop import AgentLoop  # noqa: E402
from agent_bridge.memory.http import HttpMemoryClient  # noqa: E402
from agent_bridge.policy import FULL  # noqa: E402

RULE = "─" * 74
HUMAN_NOTE = "I ran nmap and port 80 is open."


class CheckFailed(AssertionError):
    """帶步驟編號的失敗,讓輸出直接指出是第幾步斷的。"""


class Steps:
    """記錄每一步的結果,最後印一份總表。"""

    def __init__(self) -> None:
        self.rows: list[tuple[int, str, str]] = []

    def ok(self, index: int, title: str, detail: str = "") -> None:
        self.rows.append((index, title, detail))
        print(f"  ✓ {index:>2}. {title}" + (f"    {detail}" if detail else ""))

    def check(self, index: int, title: str, condition: bool, detail: str = "") -> None:
        if not condition:
            print(f"  ✗ {index:>2}. {title}    {detail}")
            raise CheckFailed(f"第 {index} 步失敗:{title} — {detail}")
        self.ok(index, title, detail)


def count_items(snapshot: dict[str, Any], layer: str) -> int:
    sections = snapshot.get(layer) or {}
    return sum(len(v or []) for v in sections.values())


def item_texts(snapshot: dict[str, Any], layer: str) -> list[str]:
    out: list[str] = []
    for items in (snapshot.get(layer) or {}).values():
        for item in items or []:
            text = (item.get("text") or "").strip().replace("\n", " ")
            if text:
                out.append(f"{item.get('id', '?')}  {text[:80]}")
    return out


def first_assumption_id(snapshot: dict[str, Any]) -> str | None:
    for items in (snapshot.get("assumption") or {}).values():
        for item in items or []:
            if item.get("id"):
                return str(item["id"])
    return None


async def main_async(args: argparse.Namespace) -> int:
    steps = Steps()
    memory = HttpMemoryClient(args.base_url)
    challenge_id = args.challenge or f"demo-web-{uuid.uuid4().hex[:8]}"

    try:
        await memory.healthz()
    except MemoryCoreError as exc:
        print(f"連不上 Memory Core:{exc}")
        print("先起服務:python -m memory_core --fake-summarizer --port 8777")
        await memory.close()
        return 2

    print(f"Memory Core  {args.base_url}")
    print(f"challenge    {challenge_id}")
    print(RULE)

    try:
        # ---- 1 ---------------------------------------------------------
        created = await memory.create_challenge(
            challenge_id,
            title="Demo Web",
            initial_input="A web login page is served on port 8080.",
        )
        steps.check(
            1,
            "建立 challenge",
            created.get("revision") == 0,
            f"revision={created.get('revision')} template={created.get('template')}",
        )

        # ---- 2 ---------------------------------------------------------
        ingested = await memory.ingest(
            challenge_id,
            {
                "source": {"type": "human", "actor": "integration-check"},
                "input": {"type": "note", "content": HUMAN_NOTE},
            },
        )
        steps.ok(2, "Human note 送進 ingest", f"「{HUMAN_NOTE}」")

        # ---- 3、4 ------------------------------------------------------
        before, after = ingested.get("revision_before"), ingested.get("revision_after")
        snapshot = await memory.get_memory(challenge_id)
        facts, assumptions = count_items(snapshot, "fact"), count_items(snapshot, "assumption")

        steps.check(
            3,
            "Summarizer 產生了 Fact 與 Assumption",
            facts >= 1 and assumptions >= 1,
            f"{facts} Fact / {assumptions} Assumption",
        )
        for layer in ("fact", "assumption"):
            for text in item_texts(snapshot, layer):
                print(f"       {layer[:1].upper()}  {text}")

        steps.check(4, "revision 0 → 1", (before, after) == (0, 1), f"{before} → {after}")

        # ---- 5 ---------------------------------------------------------
        board = render_board(snapshot, FULL)
        steps.check(5, "白板 render 得出來", bool(board.strip()), f"{len(board)} 字元")
        if args.verbose:
            print("     ┌─")
            for line in board.splitlines():
                print(f"     │ {line}")
            print("     └─")

        # ---- 6、7、8 ---------------------------------------------------
        adapter = FakeAgentAdapter(args.scenario)
        loop = AgentLoop(
            adapter=adapter, memory=memory, challenge_id=challenge_id, policy=FULL
        )
        turn = await loop.run_once("看一下這題有什麼線索。")

        steps.check(
            6,
            "Agent 拿到白板(bridge 強制注入)",
            bool(turn.board_injected),
            f"注入 {len(turn.board_injected or '')} 字元",
        )

        events = turn.result.envelope["input"]["events"]
        results = [e for e in events if e["kind"] == "tool_result"]
        evidence = [e for e in results if e.get("outcome") in {"succeeded", "failed"}]
        steps.check(
            7,
            "Agent 回了 normalized run log",
            len(events) > 0,
            f"{len(events)} 個事件,{len(results)} 個 tool_result,{len(evidence)} 個算直接證據",
        )

        before, after = turn.ingest.get("revision_before"), turn.ingest.get("revision_after")
        steps.check(8, "revision 1 → 2", (before, after) == (1, 2), f"{before} → {after}")

        # ---- 9 ---------------------------------------------------------
        snapshot = await memory.get_memory(challenge_id)
        current = revision(snapshot)
        target = first_assumption_id(snapshot)
        steps.check(9, "白板上有可以 promote 的項目", target is not None, f"挑中 {target}")

        promoted = await memory.mutate(
            challenge_id,
            expected_revision=current,
            actor="integration-check",
            reason="Verified manually",
            operations=[{"op": "promote", "id": target, "to": "fact"}],
        )
        diff = promoted.get("diff") or {}
        steps.check(
            9,
            "Human promote 成功",
            promoted.get("revision_after") == current + 1,
            f"rev {current} → {promoted.get('revision_after')}  promoted={diff.get('promoted')}",
        )

        # ---- 10 --------------------------------------------------------
        try:
            await memory.mutate(
                challenge_id,
                expected_revision=current,  # 已經過期了
                actor="integration-check",
                reason="Should be rejected",
                operations=[{"op": "promote", "id": target, "to": "fact"}],
            )
        except MemoryCoreError as exc:
            steps.check(
                10,
                "stale revision 被擋下",
                exc.status == 409 and exc.is_revision_conflict,
                f"{exc.status} {exc.code}  服務說現在是 rev {exc.current_revision}",
            )
        else:
            raise CheckFailed("第 10 步失敗:過期的 expected_revision 竟然被接受了")

        # ---- 11 --------------------------------------------------------
        one = await memory.guess(challenge_id, count=3, seed=42)
        two = await memory.guess(challenge_id, count=3, seed=42)
        other = await memory.guess(challenge_id, count=3, seed=7)

        ids_one = [c["id"] for c in one.get("candidates") or []]
        ids_two = [c["id"] for c in two.get("candidates") or []]
        ids_other = [c["id"] for c in other.get("candidates") or []]

        steps.check(11, "seed=42 兩次結果相同", ids_one == ids_two, str(ids_one))
        steps.check(
            11,
            "換 seed 結果會變",
            ids_one != ids_other,
            f"seed=7 → {ids_other}",
        )

    except CheckFailed as exc:
        print(RULE)
        print(f"整合測試失敗:{exc}")
        await memory.close()
        return 1
    except MemoryCoreError as exc:
        # 沒被任何一步預期到的 HTTP 錯誤。印乾淨的訊息,不要吐 traceback ——
        # 這支腳本是給人看的,不是給除錯器看的。
        print(RULE)
        print(f"整合測試中斷:Memory Core 回了 {exc.status} [{exc.code}]")
        print(f"  {exc}")
        if exc.status == 409 and exc.code == "challenge_exists":
            print("  提示:這個 challenge 已經有狀態了。不要指定 --challenge,讓它每次開新的。")
        await memory.close()
        return 1

    # ---- 收尾 ----------------------------------------------------------
    snapshot = await memory.get_memory(challenge_id)
    print(RULE)
    print(
        f"11 步全過。白板現況:rev {revision(snapshot)}  "
        f"{count_items(snapshot, 'fact')} Fact / {count_items(snapshot, 'assumption')} Assumption"
    )
    await memory.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="起點文件 §10 的整合測試")
    parser.add_argument("--base-url", default="http://127.0.0.1:8777")
    parser.add_argument("--challenge", help="預設每次跑一個新的,避免撞到舊狀態")
    parser.add_argument("--scenario", default="multi_tool", help="重播哪一份執行紀錄")
    parser.add_argument("--verbose", action="store_true", help="把注入的白板印出來")
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
