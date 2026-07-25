"""讀 `MemorySnapshot` 與 `MemoryDiff` 的小工具。

這兩個形狀是 Memory Core 擁有的（`spec/memory.schema.json`、
`spec/openapi.yaml` 的 `MemoryDiff`），我這邊只讀不定義，
所以這裡全部拿 dict 操作，不再包一層自己的 domain model ——
contract 一改，要跟著改的地方越少越好。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

LAYERS = ("fact", "assumption")

# MemoryDiff 裡代表「有東西動了」的欄位
DIFF_ID_FIELDS = (
    "fact_added",
    "fact_updated",
    "fact_removed",
    "assumption_added",
    "assumption_updated",
    "assumption_removed",
    "promoted",
    "demoted",
)


def is_empty_diff(diff: dict[str, Any] | None) -> bool:
    """這份 diff 有沒有真的改動白板。

    停損（決定 8：連續 N 圈空 diff 就停）與「no-op ingest 不燒 revision」
    （protocol.md §5）都靠這個判斷。
    """
    if not diff:
        return True
    if diff.get("template_changed"):
        return False
    return not any(diff.get(field) for field in DIFF_ID_FIELDS)


def iter_items(snapshot: dict[str, Any], layer: str) -> Iterator[tuple[str, dict[str, Any]]]:
    """走訪某一層的所有項目，回傳 (section, item)。"""
    for section, items in (snapshot.get(layer) or {}).items():
        for item in items or []:
            yield section, item


def index_by_id(snapshot: dict[str, Any]) -> dict[str, tuple[str, str, dict[str, Any]]]:
    """`{item_id: (layer, section, item)}` —— 渲染 diff 時要靠 ID 反查文字。

    diff 只給 ID，不給內容。
    """
    index: dict[str, tuple[str, str, dict[str, Any]]] = {}
    for layer in LAYERS:
        for section, item in iter_items(snapshot, layer):
            item_id = item.get("id")
            if item_id:
                index[item_id] = (layer, section, item)
    return index


def count_items(snapshot: dict[str, Any], layer: str) -> int:
    return sum(1 for _ in iter_items(snapshot, layer))


def revision(snapshot: dict[str, Any]) -> int:
    return int(snapshot.get("revision") or 0)
