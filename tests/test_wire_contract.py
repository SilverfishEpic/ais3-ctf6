"""送出去的 envelope 必須符合凍結的 contract。

契約來源：`origin/memory-system` 的 `spec/openapi.yaml`
（`AgentRunEvent` / `RunLogInput` / `InputSource` / `IngestEnvelope`）。

這裡的常數是那份 schema 的鏡像。**branch 合併之後要改成直接讀 spec/，
不要讓兩邊各自漂移。** 在那之前，這是離線能做的最接近的檢查；
真正的權威驗證是對 Memory Core 的 mock server 實際 POST 一次。

為什麼這組測試存在：三個關鍵 schema 都是 `additionalProperties: false`，
多帶任何一個欄位就是 422。M1 一開始的輸出有八處違規，就是被這種檢查抓到的。
"""

from __future__ import annotations

import json

import pytest

from agent_bridge.adapters.fake import FakeAgentAdapter
from agent_bridge.envelope import build_envelope
from agent_bridge.normalize import load_rows, normalize
from tests.test_normalize import FIXTURE_DIR, FIXTURES

# ---- spec/openapi.yaml 的鏡像 -----------------------------------------

EVENT_KEYS = {"seq", "role", "kind", "name", "content"}
EVENT_REQUIRED = {"seq", "role", "kind", "content"}
ROLES = {"assistant", "tool", "user", "system"}
KINDS = {"message", "tool_call", "tool_result"}

RUN_LOG_KEYS = {"type", "events"}
SOURCE_KEYS = {"type", "actor", "provider", "model", "run_id", "session_id"}
ENVELOPE_KEYS = {"source", "input"}


def envelope(name: str) -> dict:
    return FakeAgentAdapter(name).run_sync()


# ---- 結構 --------------------------------------------------------------


@pytest.mark.parametrize("name", FIXTURES)
def test_envelope_has_only_allowed_top_level_keys(name: str) -> None:
    assert set(envelope(name)) == ENVELOPE_KEYS


@pytest.mark.parametrize("name", FIXTURES)
def test_source_has_only_allowed_keys(name: str) -> None:
    source = envelope(name)["source"]
    assert set(source) <= SOURCE_KEYS
    assert source["type"] == "agent"


@pytest.mark.parametrize("name", FIXTURES)
def test_run_log_has_only_allowed_keys(name: str) -> None:
    payload = envelope(name)["input"]
    assert set(payload) == RUN_LOG_KEYS
    assert payload["type"] == "run_log"


@pytest.mark.parametrize("name", FIXTURES)
def test_run_meta_never_reaches_the_wire(name: str) -> None:
    """run_meta 在 RunLogInput 裡沒有位置，它只是本地的 loop telemetry。"""
    body = json.dumps(envelope(name), ensure_ascii=False)
    assert "run_meta" not in body
    assert "total_cost_usd" not in body


@pytest.mark.parametrize("name", FIXTURES)
def test_every_event_conforms(name: str) -> None:
    for event in envelope(name)["input"]["events"]:
        extra = set(event) - EVENT_KEYS
        assert not extra, f"{name}: 事件多帶了 contract 不允許的欄位 {sorted(extra)}"
        assert EVENT_REQUIRED <= set(event)
        assert event["role"] in ROLES
        assert event["kind"] in KINDS
        assert isinstance(event["seq"], int) and event["seq"] >= 1
        assert isinstance(event["content"], str)


@pytest.mark.parametrize("name", FIXTURES)
def test_seq_is_dense_after_projection(name: str) -> None:
    seqs = [e["seq"] for e in envelope(name)["input"]["events"]]
    assert seqs == list(range(1, len(seqs) + 1))


@pytest.mark.parametrize("name", FIXTURES)
def test_internal_only_fields_are_stripped(name: str) -> None:
    body = json.dumps(envelope(name), ensure_ascii=False)
    for field in ("outcome", "tool_use_id", "duration_ms", "original_bytes", "truncated"):
        assert f'"{field}"' not in body


# ---- R2：沒執行過的東西不能算證據 --------------------------------------


def test_denied_tool_is_not_sent_as_a_tool_result() -> None:
    """Memory Core 的 R2 只看有沒有 tool_result，不看指令有沒有真的跑。

    被沙箱擋下的指令從來沒執行，把它當 tool_result 送出去，
    Summarizer 就會把「rm 不在白名單」當成關於這一題的直接證據。
    """
    events = envelope("sandbox_denied")["input"]["events"]

    assert not any(e["kind"] == "tool_result" for e in events), (
        "整輪都沒有真的跑成功的工具，就不該有任何 tool_result 事件"
    )

    blocked = [e for e in events if "[not executed" in e["content"]]
    assert len(blocked) == 1
    assert blocked[0]["role"] == "system"
    assert blocked[0]["kind"] == "message"
    assert "blocked by sandbox" in blocked[0]["content"]


def test_interrupted_tool_is_not_sent_as_a_tool_result() -> None:
    events = envelope("interrupted")["input"]["events"]

    interrupted = [e for e in events if "interrupted by user" in e["content"]]
    assert interrupted, "被中斷的工具呼叫要標示出來"
    assert all(e["kind"] == "message" for e in interrupted)

    # 但中斷前真的跑完的工具還是 tool_result —— 那是真證據，不能一起丟掉
    assert any(e["kind"] == "tool_result" for e in events)


def test_successful_tools_stay_tool_results() -> None:
    for name in ("single_tool", "multi_tool"):
        events = envelope(name)["input"]["events"]
        assert any(e["kind"] == "tool_result" for e in events), name


def test_failed_command_stays_a_tool_result() -> None:
    """指令跑了但 exit 非 0：那是真的執行過，是關於題目的證據。"""
    events = envelope("tool_failure")["input"]["events"]
    results = [e for e in events if e["kind"] == "tool_result"]
    assert len(results) == 1
    assert "No such file or directory" in results[0]["content"]
    assert "[not executed" not in results[0]["content"]


# ---- 邊界 --------------------------------------------------------------


def test_projection_rejects_unknown_kind() -> None:
    """未來新增 kind 時，要在這裡爆炸，而不是安靜地送出去被 422。"""
    from agent_bridge.normalize import AgentRunEvent

    run = normalize(load_rows(FIXTURE_DIR / "single_tool.jsonl"))
    run.events.append(AgentRunEvent(seq=99, role="assistant", kind="telepathy", content="x"))

    with pytest.raises(AssertionError, match="kind"):
        build_envelope(run)


@pytest.mark.parametrize("name", FIXTURES)
def test_envelope_is_json_serializable(name: str) -> None:
    json.dumps(envelope(name), ensure_ascii=False)
