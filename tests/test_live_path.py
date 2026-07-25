"""生產路徑的回歸測試。

背景：`rows_from_live()` 曾經是死程式碼 —— 錄音走 `JsonlRecorder.write()`，
「正式執行」走 `rows_from_live()`，**兩條各自獨立的組列程式碼，只有一條被測到**。
當時「fixture 測到的就是生產路徑」這句話其實是假的。

現在 `ClaudeAdapter` 真的用它了（已對真 Claude 跑過一輪並被 Memory Core 接受），
這組測試是防止兩條路徑再度漂移的守門員。

這裡不呼叫 Claude，用 SDK 的真實類別自己組一輪，所以完全離線。
"""

from __future__ import annotations

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from agent_bridge.collect import ToolRecord
from agent_bridge.envelope import build_envelope
from agent_bridge.normalize import load_rows, normalize, rows_from_live
from tests.test_normalize import FIXTURE_DIR

TID = "toolu_LIVE0001"


def messages() -> list:
    return [
        AssistantMessage(
            content=[ThinkingBlock(thinking="", signature="sig")],
            model="claude-opus-4-8",
        ),
        AssistantMessage(
            content=[ToolUseBlock(id=TID, name="Bash", input={"command": "ls -la"})],
            model="claude-opus-4-8",
        ),
        UserMessage(
            content=[
                ToolResultBlock(tool_use_id=TID, content="total 2\nchall.txt", is_error=False)
            ]
        ),
        AssistantMessage(
            content=[TextBlock(text="目錄裡有 chall.txt")], model="claude-opus-4-8"
        ),
        ResultMessage(
            subtype="success",
            duration_ms=9217,
            duration_api_ms=8000,
            is_error=False,
            num_turns=2,
            session_id="live-session",
            stop_reason="end_turn",
            total_cost_usd=0.1,
            usage={"input_tokens": 10},
            result="done",
            structured_output=None,
            terminal_reason="completed",
        ),
    ]


def records() -> list[ToolRecord]:
    return [
        ToolRecord(
            seq=1,
            event="PostToolUse",
            tool_use_id=TID,
            tool_name="Bash",
            tool_input={"command": "ls -la"},
            session_id="live-session",
            finished_at=1_700_000_000.0,
            tool_response={"stdout": "total 2\nchall.txt", "stderr": ""},
            duration_ms=781.0,
        )
    ]


def live_rows() -> list[dict]:
    return rows_from_live(messages(), records())


# ---- 兩條路徑的形狀要一致 ----------------------------------------------


def test_live_rows_have_the_keys_normalize_reads() -> None:
    for row in live_rows():
        assert {"kind", "type", "data"} <= set(row)


@pytest.mark.parametrize("fixture", ["single_tool", "multi_tool"])
def test_hook_row_data_matches_the_recorded_shape(fixture: str) -> None:
    """hook 那一列的 data 欄位，錄音與正式執行必須一模一樣。

    兩邊都是 `jsonable(ToolRecord)`，這個測試就是防止哪天有人只改一邊。
    """
    live = next(r for r in live_rows() if r["kind"] == "hook")
    recorded = next(
        r for r in load_rows(FIXTURE_DIR / f"{fixture}.jsonl") if r["kind"] == "hook"
    )
    assert set(live["data"]) == set(recorded["data"])


def test_message_row_data_matches_the_recorded_shape() -> None:
    live = next(r for r in live_rows() if r["type"] == "AssistantMessage")
    recorded = next(
        r
        for r in load_rows(FIXTURE_DIR / "single_tool.jsonl")
        if r["type"] == "AssistantMessage"
    )
    assert set(live["data"]) == set(recorded["data"])


def test_content_blocks_keep_their_type_tag() -> None:
    """少了 __type__ 就分不出 TextBlock / ToolUseBlock，轉換會整個垮掉。"""
    live = next(r for r in live_rows() if r["type"] == "AssistantMessage")
    assert all("__type__" in block for block in live["data"]["content"])


# ---- 轉換結果 ----------------------------------------------------------


def test_live_rows_normalize_into_the_expected_events() -> None:
    run = normalize(live_rows())
    kinds = [(e.role, e.kind) for e in run.events]
    assert kinds == [
        ("assistant", "tool_call"),
        ("tool", "tool_result"),
        ("assistant", "message"),
    ]

    result = run.events[1]
    assert result.outcome == "ok"
    assert result.duration_ms == 781.0          # hook 有接上
    assert "chall.txt" in result.content
    assert run.run_meta["model"] == "claude-opus-4-8"
    assert run.run_meta["total_cost_usd"] == 0.1


def test_hook_position_in_the_row_list_does_not_matter() -> None:
    """`rows_from_live` 把 hook 全排在訊息後面，錄音檔是照時間交錯的。

    兩種排法必須得到同一個結果，否則生產路徑與 fixture 就不等價了。
    """
    rows = live_rows()
    interleaved = [rows[1], rows[-1], *rows[:1], *rows[2:-1]]
    assert normalize(rows).to_dict() == normalize(interleaved).to_dict()


def test_live_run_produces_a_contract_valid_envelope() -> None:
    envelope = build_envelope(normalize(live_rows()))
    assert set(envelope) == {"source", "input"}
    assert envelope["input"]["type"] == "run_log"
    for event in envelope["input"]["events"]:
        assert set(event) <= {"seq", "role", "kind", "name", "content"}
    assert envelope["source"]["model"] == "claude-opus-4-8"
