"""不需要 Memory Core 也能跑的假白板。

用途有兩個：

1. **CI** —— 完全離線、結果固定，不需要起服務
2. **ablation 的對照** —— 白板內容寫死，就能單獨測「注入的形式」對 Agent 的影響，
   不會被 Summarizer 每次產出不同內容干擾

它刻意**不模擬 Summarizer**：ingest 只是把 envelope 收下、revision +1、回一個空 diff。
要驗證真正的分類行為，請用 Memory Core 的 `--fake-summarizer` mock server ——
那個跑的是跟正式版一樣的 epistemic policy。
"""

from __future__ import annotations

import copy
from typing import Any

from memory.board import is_empty_diff
from utils.errors import MemoryCoreError

EMPTY_DIFF: dict[str, Any] = {
    "fact_added": [],
    "fact_updated": [],
    "fact_removed": [],
    "assumption_added": [],
    "assumption_updated": [],
    "assumption_removed": [],
    "promoted": [],
    "demoted": [],
    "template_changed": None,
}


def demo_snapshot(challenge_id: str = "demo-web") -> dict[str, Any]:
    """一份形狀符合 `spec/memory.schema.json` 的示範白板。"""
    return {
        "schema_version": 2,
        "revision": 3,
        "challenge": {
            "id": challenge_id,
            "title": "Demo Web",
            "category": "web",
            "template": "web",
            "template_version": 1,
            "summary": "登入頁在 8080，正在確認 session 綁定方式。",
        },
        "fact": {
            "target": [
                {
                    "id": "F001",
                    "text": "TCP/8080 is open and serves an HTTP login page.",
                    "evidence": ["I-83b70a9a"],
                    "source_actor": "agent",
                    "tags": [],
                }
            ],
            "authentication": [
                {
                    "id": "F002",
                    "text": "The login form posts username and password as JSON.",
                    "evidence": ["I-83b70a9a"],
                    "source_actor": "human",
                    "tags": [],
                }
            ],
        },
        "assumption": {
            "vulnerability_hypotheses": [
                {
                    "id": "A001",
                    "text": "The session cookie may not be regenerated after login.",
                    "evidence": ["I-42aa8e92"],
                    "tags": [],
                }
            ],
        },
        "guessing": {
            "catalog": "web@1",
            "state": {"WEB-SQLI": {"tried": True}, "WEB-SSRF": {"tried": False}},
        },
        "meta": {"last_input_id": "I-42aa8e92", "fact_seq": 2, "assumption_seq": 1},
    }


class FakeMemoryClient:
    """`MemoryClient` 的離線實作。"""

    def __init__(self, snapshot: dict[str, Any] | None = None) -> None:
        self._challenges: dict[str, dict[str, Any]] = {}
        self._template = snapshot
        self.ingested: list[dict[str, Any]] = []
        self.calls: list[tuple[str, str]] = []

    # ---- 端點 ------------------------------------------------------

    async def create_challenge(
        self,
        challenge_id: str,
        *,
        title: str | None = None,
        initial_input: str | None = None,
        template: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(("create_challenge", challenge_id))
        snapshot = copy.deepcopy(self._template or demo_snapshot(challenge_id))
        snapshot["challenge"]["id"] = challenge_id
        if title:
            snapshot["challenge"]["title"] = title
        if template:
            snapshot["challenge"]["template"] = template
        self._challenges[challenge_id] = snapshot
        return snapshot

    async def get_memory(
        self,
        challenge_id: str,
        *,
        layer: str | None = None,
        section: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(("get_memory", challenge_id))
        snapshot = copy.deepcopy(self._require(challenge_id))
        if layer:
            for other in ("fact", "assumption", "guessing"):
                if other != layer:
                    snapshot[other] = {} if other != "guessing" else snapshot["guessing"]
        if section and layer in ("fact", "assumption"):
            snapshot[layer] = {section: snapshot.get(layer, {}).get(section, [])}
        return snapshot

    async def ingest(self, challenge_id: str, envelope: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("ingest", challenge_id))
        snapshot = self._require(challenge_id)
        self.ingested.append(envelope)

        queued: list[dict[str, Any]] = snapshot.setdefault("_queued_diffs", [])
        diff = queued.pop(0) if queued else copy.deepcopy(EMPTY_DIFF)

        before = snapshot["revision"]
        # 空 diff 不燒 revision（protocol.md §5：no-op ingest 不進版）
        after = before if is_empty_diff(diff) else before + 1
        snapshot["revision"] = after
        return {
            "revision_before": before,
            "revision_after": after,
            "summary": None,
            "diff": diff,
            "source_id": f"I-fake{len(self.ingested):04d}",
        }

    async def query(
        self,
        challenge_id: str,
        question: str,
        *,
        layers: list[str] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(("query", challenge_id))
        snapshot = self._require(challenge_id)
        return {
            "answer": f"(fake) 針對「{question}」目前白板沒有更多資訊。",
            "references": [],
            "revision": snapshot["revision"],
        }

    async def guess(
        self,
        challenge_id: str,
        *,
        count: int = 3,
        only_untried: bool = True,
        seed: int | None = None,
    ) -> dict[str, Any]:
        self.calls.append(("guess", challenge_id))
        snapshot = self._require(challenge_id)
        state = snapshot["guessing"]["state"]
        ids = [tid for tid, s in state.items() if not (only_untried and s.get("tried"))]
        return {
            "catalog": snapshot["guessing"]["catalog"],
            "revision": snapshot["revision"],
            "candidates": [
                {"id": tid, "title": tid.replace("-", " ").title(), "tags": []}
                for tid in sorted(ids)[:count]
            ],
        }

    async def close(self) -> None:
        return None

    # ---- 測試用 ----------------------------------------------------

    def queue_diffs(self, challenge_id: str, *diffs: dict[str, Any]) -> None:
        """排隊安排接下來幾次 ingest 要回什麼 diff。

        停損（決定 8：連續 N 圈空 diff 就停）沒有這個就測不了。
        排完之後的 ingest 一律回空 diff。
        """
        snapshot = self._require(challenge_id)
        queued: list[dict[str, Any]] = snapshot.setdefault("_queued_diffs", [])
        for diff in diffs:
            merged = copy.deepcopy(EMPTY_DIFF)
            merged.update(diff)
            queued.append(merged)

    def _require(self, challenge_id: str) -> dict[str, Any]:
        if challenge_id not in self._challenges:
            raise MemoryCoreError(
                f"challenge {challenge_id} 不存在（假的 client 要先 create_challenge）",
                status=404,
                code="challenge_not_found",
            )
        return self._challenges[challenge_id]
