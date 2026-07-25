"""白板與 diff 的渲染（決定 3、4、5）。

消融實驗的正確性直接靠這裡：`fact_only` 那一檔如果不小心把 assumption 也渲染出去，
整組實驗數字就沒有意義了。
"""

from __future__ import annotations

from agent_bridge.inject import render_board, render_diff
from agent_bridge.memory.fake import demo_snapshot
from agent_bridge.policy import (
    FACT_AND_ASSUMPTION,
    FACT_ONLY,
    FULL,
    NO_MEMORY,
)

SNAPSHOT = demo_snapshot()

DIFF = {
    "fact_added": ["F003"],
    "fact_updated": [],
    "fact_removed": [],
    "assumption_added": ["A002"],
    "assumption_updated": [],
    "assumption_removed": [],
    "promoted": ["A001"],
    "demoted": [],
    "template_changed": None,
}

GROWN = {
    **SNAPSHOT,
    "revision": 5,
    "fact": {
        **SNAPSHOT["fact"],
        "endpoints": [{"id": "F003", "text": "POST /api/login accepts JSON.", "evidence": []}],
    },
    "assumption": {
        "vulnerability_hypotheses": [
            *SNAPSHOT["assumption"]["vulnerability_hypotheses"],
            {"id": "A002", "text": "The API may not rate-limit login attempts.", "evidence": []},
        ]
    },
}


# ---- 整份白板 ----------------------------------------------------------


def test_full_board_has_everything() -> None:
    text = render_board(SNAPSHOT, FULL)
    assert "F001" in text and "TCP/8080" in text
    assert "A001" in text
    assert "web@1" in text
    assert "rev 3" in text
    assert "登入頁在 8080" in text  # challenge summary


def test_fact_only_hides_assumptions() -> None:
    text = render_board(SNAPSHOT, FACT_ONLY)
    assert "F001" in text
    assert "A001" not in text
    assert "ASSUMPTION" not in text
    assert "web@1" not in text


def test_fact_and_assumption_hides_guessing() -> None:
    text = render_board(SNAPSHOT, FACT_AND_ASSUMPTION)
    assert "F001" in text and "A001" in text
    assert "GUESSING" not in text


def test_no_memory_renders_nothing() -> None:
    assert render_board(SNAPSHOT, NO_MEMORY) == ""
    assert render_diff(DIFF, GROWN, NO_MEMORY) == ""


def test_board_tells_the_agent_it_cannot_write() -> None:
    """Agent 找不到「儲存」的工具會空轉，所以要講明白。"""
    text = render_board(SNAPSHOT, FULL)
    assert "不能" in text and "自動送去整理" in text


def test_empty_board_says_so_instead_of_looking_broken() -> None:
    empty = {
        "revision": 0,
        "challenge": {"id": "x", "title": "X"},
        "fact": {},
        "assumption": {},
        "guessing": {"catalog": "generic@1", "state": {}},
    }
    text = render_board(empty, FULL)
    assert "空的" in text


def test_guess_candidates_bring_titles() -> None:
    """snapshot 只有裸 ID，/guess 抽出來的才有標題。"""
    candidates = [{"id": "WEB-SSRF", "title": "Server-Side Request Forgery"}]
    text = render_board(SNAPSHOT, FULL, guess_candidates=candidates)
    assert "Server-Side Request Forgery" in text


# ---- diff --------------------------------------------------------------


def test_diff_shows_text_not_just_ids() -> None:
    """diff 本身只有 ID，要靠 snapshot 反查文字，否則 Agent 看不懂。"""
    text = render_diff(DIFF, GROWN, FULL, revision_before=3)
    assert "F003" in text and "POST /api/login" in text
    assert "A002" in text and "rate-limit" in text
    assert "rev 3 → 5" in text


def test_diff_reports_promotion() -> None:
    text = render_diff(DIFF, GROWN, FULL, revision_before=3)
    assert "A001" in text and "升格" in text


def test_diff_warns_that_older_snapshots_are_stale() -> None:
    """same-session 最大的坑：第一輪的快照還躺在 context 裡跟現況打架。"""
    text = render_diff(DIFF, GROWN, FULL, revision_before=3)
    assert "過期" in text and "memory_show" in text


def test_diff_respects_layers() -> None:
    text = render_diff(DIFF, GROWN, FACT_ONLY, revision_before=3)
    assert "F003" in text
    assert "A002" not in text


def test_empty_diff_renders_nothing() -> None:
    """白板沒動就不要塞一段空標題進 prompt。"""
    empty = {k: ([] if isinstance(v, list) else None) for k, v in DIFF.items()}
    assert render_diff(empty, GROWN, FULL) == ""


def test_diff_survives_ids_that_left_the_board() -> None:
    """被移除的項目在新 snapshot 裡查不到，不能因此爆掉。"""
    diff = {**DIFF, "fact_removed": ["F999"], "fact_added": [], "assumption_added": []}
    text = render_diff(diff, GROWN, FULL, revision_before=3)
    assert "F999" in text


def test_template_change_is_reported() -> None:
    diff = {**DIFF, "template_changed": {"from": "generic@1", "to": "web@1"}}
    text = render_diff(diff, GROWN, FULL, revision_before=3)
    assert "web@1" in text and "題型" in text
