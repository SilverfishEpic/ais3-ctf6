"""轉換的回歸測試。

分兩層：

1. **快照** —— 對六份真實執行紀錄跑轉換，跟存檔比對。SDK 一改版就會紅，
   而且直接指出是哪個事件變了。更新方式：`UPDATE_SNAPSHOTS=1 pytest`。
2. **語意不變量** —— 快照只保證「跟上次一樣」，這一層保證「一樣是對的」。
   五種結局的分類尤其重要：Memory Core 的 R2 只認 `succeeded` / `failed`，
   把 denied 當成 succeeded，Summarizer 就會把「rm 不在白名單」
   寫進題目的白板。

全部離線，不需要網路也不需要 API key。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agent_bridge.adapters.fake import FakeAgentAdapter
from agent_bridge.normalize import NormalizedRun, load_rows, normalize

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "fixtures" / "claude"
SNAPSHOT_DIR = Path(__file__).resolve().parent / "snapshots"

FIXTURES = [
    "talk_only",
    "single_tool",
    "multi_tool",
    "tool_failure",
    "sandbox_denied",
    "interrupted",
]


def run(name: str) -> NormalizedRun:
    return normalize(load_rows(FIXTURE_DIR / f"{name}.jsonl"))


def tool_results(name: str) -> list:
    return [e for e in run(name).events if e.kind == "tool_result"]


# ---- 1. 快照 ----------------------------------------------------------


@pytest.mark.parametrize("name", FIXTURES)
def test_snapshot(name: str) -> None:
    actual = run(name).to_dict()
    path = SNAPSHOT_DIR / f"{name}.json"

    if os.environ.get("UPDATE_SNAPSHOTS"):
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(actual, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        pytest.skip(f"已更新快照 {path.name}")

    assert path.exists(), f"缺少快照 {path}，用 UPDATE_SNAPSHOTS=1 pytest 產生"
    assert actual == json.loads(path.read_text(encoding="utf-8"))


# ---- 2. 語意不變量 ----------------------------------------------------


@pytest.mark.parametrize("name", FIXTURES)
def test_every_tool_result_pairs_with_a_tool_call(name: str) -> None:
    """tool_use_id 必須對得上 —— 對不上就代表事件配錯了。"""
    events = run(name).events
    call_ids = {e.tool_use_id for e in events if e.kind == "tool_call"}
    result_ids = {e.tool_use_id for e in events if e.kind == "tool_result"}
    assert result_ids <= call_ids


@pytest.mark.parametrize("name", FIXTURES)
def test_thinking_never_leaks_into_events(name: str) -> None:
    """思考內容永遠是空的（m0-findings §2），不該變成空白事件汙染白板。"""
    normalized = run(name)
    assert all(e.kind != "thinking" for e in normalized.events)
    assert all(e.content.strip() for e in normalized.events)
    # 但「它想了幾次」要留在 meta 裡，訊號不能丟
    assert "thinking_blocks" in normalized.run_meta


@pytest.mark.parametrize("name", FIXTURES)
def test_seq_is_dense_and_ordered(name: str) -> None:
    seqs = [e.seq for e in run(name).events]
    assert seqs == list(range(1, len(seqs) + 1))


def test_talk_only_has_no_tools() -> None:
    events = run("talk_only").events
    assert [e.kind for e in events] == ["message"]


def test_single_tool_shape() -> None:
    events = run("single_tool").events
    kinds = [e.kind for e in events]
    assert kinds.count("tool_call") == 1
    assert kinds.count("tool_result") == 1

    call = next(e for e in events if e.kind == "tool_call")
    assert call.name == "Bash"
    assert call.input["command"] == "ls -la"   # 結構化的真相（contract Q4）
    assert call.content == "ls -la"            # 人類可讀的那份


def test_successful_tool_is_succeeded_and_timed() -> None:
    result = tool_results("single_tool")[0]
    assert result.outcome == "succeeded"
    assert result.is_error is None
    # hook 有跑過才有耗時 —— 這也證明指令真的執行了
    assert result.duration_ms and result.duration_ms > 0


def test_failed_command_is_failed_not_denied() -> None:
    """指令跑了但 exit 非 0：這是關於題目的證據，不能跟被擋下混為一談。"""
    result = tool_results("tool_failure")[0]
    assert result.outcome == "failed"
    assert result.is_error is True
    assert "No such file or directory" in result.content
    assert result.duration_ms is not None  # 真的執行過


def test_denied_command_never_ran() -> None:
    """被沙箱擋下：**不是**關於題目的證據，而且要看得出它沒執行。"""
    result = tool_results("sandbox_denied")[0]
    assert result.outcome == "denied"
    # 沒有 hook 紀錄 = 沒有耗時 = 指令根本沒跑
    assert result.duration_ms is None
    assert "白名單" in result.content


def test_interrupted_run_is_flagged_and_keeps_completed_work() -> None:
    """規劃書 §14：中止後已完成的部分仍要送進 ingest。"""
    normalized = run("interrupted")
    results = [e for e in normalized.events if e.kind == "tool_result"]

    assert normalized.run_meta["interrupted"] is True
    assert any(r.outcome == "interrupted" for r in results)
    # 被打斷之前跑完的工具要留著，那是真的證據
    completed = [r for r in results if r.outcome == "succeeded"]
    assert completed and all(r.duration_ms for r in completed)


def test_tool_result_without_hook_provenance_is_unknown() -> None:
    """沒有 PostToolUse 紀錄就無法證明工具跑過，只能給 `unknown`。

    `unknown` 在 R2 底下不算直接證據 —— 寧可少一筆 Fact，
    也不要拿一筆來歷不明的輸出去支撐它。
    """
    rows = [row for row in load_rows(FIXTURE_DIR / "single_tool.jsonl") if row["kind"] != "hook"]
    result = next(e for e in normalize(rows).events if e.kind == "tool_result")

    assert result.outcome == "unknown"
    assert result.duration_ms is None


def test_interrupt_detection_survives_terminal_reason_variants() -> None:
    """terminal_reason 有 aborted_tools / aborted_streaming 兩種，不能寫死其一。"""
    rows = load_rows(FIXTURE_DIR / "interrupted.jsonl")
    for variant in ("aborted_tools", "aborted_streaming"):
        patched = []
        for row in rows:
            if row["kind"] == "message" and row["type"] == "ResultMessage":
                row = {**row, "data": {**row["data"], "terminal_reason": variant}}
            patched.append(row)
        assert normalize(patched).run_meta["interrupted"] is True


@pytest.mark.parametrize("name", FIXTURES)
def test_run_meta_carries_cost_not_events(name: str) -> None:
    """成本/耗時不是證據，不能混進 events[]（contract Q3 的提案）。"""
    normalized = run(name)
    assert "total_cost_usd" in normalized.run_meta
    for event in normalized.events:
        assert not hasattr(event, "cost_usd")


# ---- 3. 交付給 P1 / P2 的入口 ------------------------------------------


@pytest.mark.parametrize("name", FIXTURES)
def test_fake_adapter_produces_valid_envelope(name: str) -> None:
    envelope = FakeAgentAdapter(name).run_sync()

    assert envelope["source"]["type"] == "agent"
    assert envelope["source"]["provider"] == "fake"
    assert envelope["input"]["type"] == "run_log"
    assert isinstance(envelope["input"]["events"], list)
    # 整包要能序列化，不然 POST 不出去
    json.dumps(envelope, ensure_ascii=False)


def test_fake_adapter_lists_every_fixture() -> None:
    assert set(FakeAgentAdapter.available()) == set(FIXTURES)


def test_fake_adapter_records_injected_board() -> None:
    """P2 要能檢查白板注入有沒有生效（決定 3）。"""
    import asyncio

    adapter = FakeAgentAdapter("single_tool")
    asyncio.run(adapter.start("解這題", board="FACT\n  F001 TCP/80 開著"))
    assert adapter.calls[-1]["board"].startswith("FACT")
    assert adapter.calls[-1]["prompt"] == "解這題"


def test_fake_adapter_rejects_unknown_scenario() -> None:
    with pytest.raises(FileNotFoundError, match="可用的"):
        FakeAgentAdapter("no_such_recording").run_sync()
