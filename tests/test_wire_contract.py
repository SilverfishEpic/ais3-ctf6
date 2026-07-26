"""送出去的 envelope 必須符合凍結的 contract。

**這裡不再抄寫 schema —— 直接讀 `spec/openapi.yaml`。**

原本這個檔案手抄了一份 `AgentRunEvent` 的欄位清單當鏡像,並在註解裡寫「branch
合併之後要改成直接讀 spec/,不要讓兩邊各自漂移」。後來 spec 加了第六個欄位
`outcome`,鏡像沒跟上,整組測試就開始擋錯的東西。改成從 spec 反推,那種漂移
就不可能再發生。

為什麼這組測試存在:`IngestEnvelope` / `RunLogInput` / `AgentRunEvent` 三個
schema 都是 `additionalProperties: false`,多帶任何一個欄位就是 422。
M1 一開始的輸出有八處違規,就是被這種檢查抓到的。

真正的權威驗證仍然是對 Memory Core 實際 POST 一次 —— 見
`tools/integration_check.py`。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from agent_bridge.adapters.fake import FakeAgentAdapter
from agent_bridge.envelope import build_envelope
from agent_bridge.normalize import load_rows, normalize
from tests.test_normalize import FIXTURE_DIR, FIXTURES

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = REPO_ROOT / "spec" / "openapi.yaml"


def _schemas() -> dict[str, Any]:
    return yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))["components"]["schemas"]


SCHEMAS = _schemas()


def keys_of(name: str) -> set[str]:
    return set(SCHEMAS[name]["properties"])


def required_of(name: str) -> set[str]:
    return set(SCHEMAS[name].get("required", []))


def enum_of(name: str, field: str) -> set[str]:
    """拿一個欄位的 enum。optional 欄位包在 anyOf 裡,要往下找一層。"""
    node = SCHEMAS[name]["properties"][field]
    if "enum" in node:
        return set(node["enum"])
    for branch in node.get("anyOf", []):
        if "enum" in branch:
            return set(branch["enum"])
    raise KeyError(f"{name}.{field} 沒有 enum")


EVENT_KEYS = keys_of("AgentRunEvent")
EVENT_REQUIRED = required_of("AgentRunEvent")
ROLES = enum_of("AgentRunEvent", "role")
KINDS = enum_of("AgentRunEvent", "kind")
OUTCOMES = enum_of("AgentRunEvent", "outcome")

RUN_LOG_KEYS = keys_of("RunLogInput")
SOURCE_KEYS = keys_of("InputSource")
ENVELOPE_KEYS = keys_of("IngestEnvelope")

#: R2(protocol.md §3):只有這兩種算工具真的執行過。
EVIDENCE_OUTCOMES = {"succeeded", "failed"}


def envelope(name: str) -> dict:
    return FakeAgentAdapter(name).run_sync()


def results(name: str) -> list[dict]:
    return [e for e in envelope(name)["input"]["events"] if e["kind"] == "tool_result"]


# ---- spec 本身的前提 ---------------------------------------------------


def test_the_schemas_this_file_relies_on_are_closed() -> None:
    """三個 schema 只要有一個變成開放的,這組測試就失去意義,要立刻知道。"""
    for name in ("IngestEnvelope", "RunLogInput", "AgentRunEvent", "InputSource"):
        assert SCHEMAS[name].get("additionalProperties") is False, name


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
    assert set(payload) <= RUN_LOG_KEYS
    assert payload["type"] == "run_log"


@pytest.mark.parametrize("name", FIXTURES)
def test_run_meta_never_reaches_the_wire(name: str) -> None:
    """run_meta 在 RunLogInput 裡沒有位置,它只是本地的 loop telemetry。"""
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
        if "outcome" in event:
            assert event["outcome"] in OUTCOMES


@pytest.mark.parametrize("name", FIXTURES)
def test_seq_is_dense_after_projection(name: str) -> None:
    seqs = [e["seq"] for e in envelope(name)["input"]["events"]]
    assert seqs == list(range(1, len(seqs) + 1))


@pytest.mark.parametrize("name", FIXTURES)
def test_internal_only_fields_are_stripped(name: str) -> None:
    """`outcome` 已經是合約欄位,不在這份清單裡 —— 其餘仍然只留在本地。"""
    body = json.dumps(envelope(name), ensure_ascii=False)
    for field in ("tool_use_id", "duration_ms", "original_bytes", "truncated"):
        assert f'"{field}"' not in body


@pytest.mark.parametrize("name", FIXTURES)
def test_outcome_is_present_on_every_tool_result(name: str) -> None:
    """少一個 `outcome`,Memory Core 就當成 `unknown`,那份證據等於白丟。"""
    for event in results(name):
        assert "outcome" in event, f"{name}: tool_result 少了 outcome"


# ---- R2:沒執行過的東西不能算證據 --------------------------------------
#
# 這一區原本測的是另一套做法:bridge 自己把沒執行的工具降級成
# `role: system` + `kind: message`,讓 Memory Core 舊版的
# `any(kind == "tool_result")` 判斷不到它。
#
# 合約後來加了 `outcome`,核心的 R2 改成
# `outcome in {"succeeded", "failed"}`,那個 workaround 就不需要了,也已經移除。
# 現在的不變量是:**照實送出 tool_result,但把執行狀態誠實標在 outcome 上**,
# 由核心決定它算不算證據。


def test_denied_tool_is_a_tool_result_marked_denied() -> None:
    """被沙箱擋下的指令從來沒執行,不能取得 Fact 資格。"""
    denied = [e for e in results("sandbox_denied") if e["outcome"] == "denied"]
    assert len(denied) == 1

    assert not any(e["outcome"] in EVIDENCE_OUTCOMES for e in results("sandbox_denied")), (
        "整輪都沒有真的跑成功的工具,這份 run_log 不該有任何直接證據"
    )


def test_interrupted_tool_is_marked_but_completed_work_survives() -> None:
    outcomes = [e["outcome"] for e in results("interrupted")]
    assert "interrupted" in outcomes

    # 中斷前真的跑完的工具仍然是證據 —— 不能一起丟掉(規劃書 §14)
    assert any(o in EVIDENCE_OUTCOMES for o in outcomes)


def test_successful_tools_are_evidence() -> None:
    for name in ("single_tool", "multi_tool"):
        outcomes = [e["outcome"] for e in results(name)]
        assert outcomes and all(o == "succeeded" for o in outcomes), name


def test_failed_command_is_still_evidence() -> None:
    """指令跑了但 exit 非 0:那是真的執行過,是關於題目的證據。"""
    failed = results("tool_failure")
    assert len(failed) == 1
    assert failed[0]["outcome"] == "failed"
    assert "No such file or directory" in failed[0]["content"]


# ---- 邊界 --------------------------------------------------------------


def test_projection_rejects_unknown_kind() -> None:
    """未來新增 kind 時,要在這裡爆炸,而不是安靜地送出去被 422。"""
    from agent_bridge.normalize import AgentRunEvent

    run = normalize(load_rows(FIXTURE_DIR / "single_tool.jsonl"))
    run.events.append(AgentRunEvent(seq=99, role="assistant", kind="telepathy", content="x"))

    with pytest.raises(AssertionError, match="kind"):
        build_envelope(run)


def test_projection_rejects_unknown_outcome() -> None:
    from agent_bridge.normalize import AgentRunEvent

    run = normalize(load_rows(FIXTURE_DIR / "single_tool.jsonl"))
    run.events.append(
        AgentRunEvent(seq=99, role="tool", kind="tool_result", content="x", outcome="vibes")
    )

    with pytest.raises(AssertionError, match="outcome"):
        build_envelope(run)


@pytest.mark.parametrize("name", FIXTURES)
def test_envelope_is_json_serializable(name: str) -> None:
    json.dumps(envelope(name), ensure_ascii=False)
