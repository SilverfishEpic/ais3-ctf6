"""Bridge 的錯誤型別。

Memory Core 的 error model（protocol.md §10）::

    {"error": {"code": "revision_conflict", "message": "...", "details": {...}}}

`code` 被保留下來，因為 loop 需要分辨「這是可以重試的衝突」還是
「這是我送錯東西，重試幾次都一樣」。
"""

from __future__ import annotations

import json
from typing import Any

# 重試沒有意義的 —— 是我送錯了
CLIENT_FAULT_CODES = frozenset(
    {"invalid_request", "proposal_invalid", "challenge_not_found", "template_not_found"}
)


class BridgeError(Exception):
    """Agent Bridge 的錯誤基底。"""


class MemoryCoreError(BridgeError):
    """Memory Core 回了非 2xx，或根本連不上。"""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.details = details or {}

    @classmethod
    def from_response(cls, method: str, url: str, status: int, body: str) -> MemoryCoreError:
        code: str | None = None
        details: dict[str, Any] = {}
        message = body.strip()

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = None

        if isinstance(parsed, dict):
            error = parsed.get("error")
            if isinstance(error, dict):
                code = error.get("code")
                details = error.get("details") or {}
                message = error.get("message") or message
            elif "detail" in parsed:
                # FastAPI 的請求驗證失敗長這樣 —— 通常代表我送的欄位不合約
                message = json.dumps(parsed["detail"], ensure_ascii=False)

        return cls(
            f"{method} {url} → {status}"
            + (f" [{code}]" if code else "")
            + f": {message[:800]}",
            status=status,
            code=code,
            details=details,
        )

    @property
    def is_revision_conflict(self) -> bool:
        return self.code == "revision_conflict"

    @property
    def is_my_fault(self) -> bool:
        """重試也沒用，要改的是送出去的東西。"""
        return self.code in CLIENT_FAULT_CODES or self.status == 422

    @property
    def current_revision(self) -> int | None:
        value = self.details.get("current_revision")
        return value if isinstance(value, int) else None
