"""組 `IngestEnvelope`（規劃書 §8.2）。

Claude 專屬的東西到這裡為止 —— Memory Core 收到的已經是 vendor-neutral 的形狀。
"""

from __future__ import annotations

from typing import Any

from agent_bridge.normalize import NormalizedRun


def build_envelope(
    run: NormalizedRun,
    *,
    provider: str = "anthropic",
    model: str | None = None,
    run_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """把一輪的結果包成 Memory Core 的 `/ingest` 輸入。

    `model` / `session_id` 沒給就從 run_meta 補，因為那是最可靠的來源。

    `run_meta` 放在 `input` 底下而不是混進 `events[]`：成本、耗時、token
    這些**不是證據**，不該和工具輸出並列（contract 第 3 題）。
    """
    meta = run.run_meta
    return {
        "source": {
            "type": "agent",
            "provider": provider,
            "model": model or meta.get("model"),
            "run_id": run_id,
            "session_id": session_id or meta.get("session_id"),
        },
        "input": {
            "type": "run_log",
            "events": [e.to_dict() for e in run.events],
            "run_meta": meta,
        },
    }
