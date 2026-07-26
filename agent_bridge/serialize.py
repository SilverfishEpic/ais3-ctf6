"""把 SDK 物件轉成純 JSON 結構。

錄製與正式執行都走這個函式，所以 `fixtures/claude/*.jsonl` 裡的形狀
就是 `normalize.py` 在正式執行時看到的形狀 —— 測試才真的等於生產路徑。
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any


def jsonable(obj: Any) -> Any:
    """盡量忠實地轉成 JSON。

    dataclass 會多帶一個 `__type__`：content block 全是 dataclass，
    少了型別名稱就分不出 TextBlock / ThinkingBlock / ToolUseBlock。

    真的不認識的東西退回 repr()，寧可留一個看得懂的字串，也不要讓整份紀錄爆掉。
    """
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        out = {f.name: jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
        out["__type__"] = type(obj).__name__
        return out
    if isinstance(obj, Path):
        return str(obj)
    return repr(obj)
