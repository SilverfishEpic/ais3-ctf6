"""截斷策略的測試（決定 9）。

重點不只是「有沒有砍」，而是**砍過的東西有沒有誠實標記** ——
journal 是證據紀錄，砍了不說就是說謊。
"""

from __future__ import annotations

from agent_bridge.truncate import TruncationPolicy, apply_run_budget, truncate_text

SMALL = TruncationPolicy(head_chars=100, tail_chars=50, run_total_chars=500)


def test_short_text_is_untouched() -> None:
    result = truncate_text("hello", SMALL)
    assert result.text == "hello"
    assert result.truncated is False
    assert result.original_bytes == 5


def test_long_text_keeps_head_and_tail() -> None:
    text = "H" * 500 + "T" * 500
    result = truncate_text(text, SMALL)

    assert result.truncated is True
    assert result.text.startswith("H" * 100)
    assert result.text.endswith("T" * 50)
    assert "truncated" in result.text
    # 原始大小要記真的，不是砍完的大小
    assert result.original_bytes == 1000
    assert len(result.text) < len(text)


def test_original_bytes_counts_utf8_not_characters() -> None:
    """中文一個字三個 byte —— 記錯的話 Summarizer 對資料量的判斷就會歪。"""
    text = "中" * 10
    result = truncate_text(text, SMALL)
    assert result.original_bytes == 30


def test_boundary_exactly_at_limit_is_not_truncated() -> None:
    text = "x" * SMALL.per_event_chars
    assert truncate_text(text, SMALL).truncated is False


def test_run_budget_leaves_small_runs_alone() -> None:
    texts = ["a" * 10, "b" * 20]
    results = apply_run_budget(texts, SMALL)
    assert [r.text for r in results] == texts
    assert not any(r.truncated for r in results)


def test_run_budget_shrinks_until_under_cap() -> None:
    """單筆各自截斷後總量還是超標時，要繼續往下砍到進預算。"""
    roomy = TruncationPolicy(head_chars=3000, tail_chars=1000, run_total_chars=5000)
    results = apply_run_budget(["x" * 20_000 for _ in range(5)], roomy)

    assert sum(len(r.text) for r in results) <= roomy.run_total_chars
    assert all(r.truncated for r in results)
    # 不管砍幾次，原始大小都要保持真實
    assert all(r.original_bytes == 20_000 for r in results)


def test_run_budget_yields_to_the_floor_rather_than_shredding() -> None:
    """事件數 × 下限 > 預算時，寧可超出預算，也不要留下一堆沒用的碎片。

    這是刻意的取捨，不是 bug —— 見 TruncationPolicy 的說明。
    """
    policy = TruncationPolicy(
        head_chars=400, tail_chars=100, run_total_chars=500, min_event_chars=200
    )
    results = apply_run_budget(["x" * 5000 for _ in range(10)], policy)

    assert sum(len(r.text) for r in results) > policy.run_total_chars
    assert all(len(r.text) >= policy.min_event_chars for r in results)
    assert all(r.original_bytes == 5000 for r in results)


def test_run_budget_preserves_order_and_length() -> None:
    texts = ["a" * 5, "b" * 3000, "c" * 7]
    results = apply_run_budget(texts, SMALL)

    assert len(results) == 3
    assert results[0].text == "a" * 5
    assert results[2].text == "c" * 7


def test_tiny_head_and_tail_are_the_callers_choice() -> None:
    """把 head/tail 設得比下限還小，就是明確選擇了要小事件。

    `min_event_chars` 只管「整輪超標時最多砍到多小」，不會把單筆撐大。
    """
    tiny = TruncationPolicy(head_chars=50, tail_chars=20, run_total_chars=100)
    results = apply_run_budget(["x" * 9000 for _ in range(5)], tiny)

    assert all(r.text.startswith("x" * 50) for r in results)
    assert all(len(r.text) < tiny.min_event_chars for r in results)
    assert all(r.original_bytes == 9000 for r in results)
