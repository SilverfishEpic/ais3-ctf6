"""打真的 Memory Core。

端點以 `spec/openapi.yaml`（origin/memory-system）為準。路徑帶 `/v1/challenges/{id}`
前綴 —— 兩份設計文件曾經寫得不一樣，凍結的 contract 選了帶前綴的那個。

錯誤處理照 protocol.md §10 的 error model：

    {"error": {"code": "revision_conflict", "message": "...", "details": {...}}}
"""

from __future__ import annotations

from typing import Any

import httpx

from agent_bridge.errors import MemoryCoreError


class HttpMemoryClient:
    """`MemoryClient` 的正式實作。"""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8777",
        *,
        timeout: float = 120.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    # ---- 端點 ------------------------------------------------------

    async def create_challenge(
        self,
        challenge_id: str,
        *,
        title: str | None = None,
        initial_input: str | None = None,
        template: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"id": challenge_id, "title": title or challenge_id}
        if initial_input is not None:
            body["initial_input"] = initial_input
        if template is not None:
            body["template"] = template
        return await self._request("POST", "/v1/challenges", json=body)

    async def get_memory(
        self,
        challenge_id: str,
        *,
        layer: str | None = None,
        section: str | None = None,
    ) -> dict[str, Any]:
        params = {k: v for k, v in (("layer", layer), ("section", section)) if v}
        return await self._request(
            "GET", f"/v1/challenges/{challenge_id}/memory", params=params or None
        )

    async def ingest(self, challenge_id: str, envelope: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "POST", f"/v1/challenges/{challenge_id}/ingest", json=envelope
        )

    async def query(
        self,
        challenge_id: str,
        question: str,
        *,
        layers: list[str] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"question": question}
        if layers:
            body["layers"] = layers
        return await self._request("POST", f"/v1/challenges/{challenge_id}/query", json=body)

    async def guess(
        self,
        challenge_id: str,
        *,
        count: int = 3,
        only_untried: bool = True,
        seed: int | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"count": count, "only_untried": only_untried}
        if seed is not None:
            body["seed"] = seed
        return await self._request("POST", f"/v1/challenges/{challenge_id}/guess", json=body)

    async def healthz(self) -> dict[str, Any]:
        return await self._request("GET", "/healthz")

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ---- 內部 ------------------------------------------------------

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            response = await self._client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise MemoryCoreError(f"連不上 Memory Core（{method} {url}）：{exc}") from exc

        if response.status_code >= 400:
            raise MemoryCoreError.from_response(method, url, response.status_code, response.text)

        if not response.content:
            return {}
        return response.json()

    async def __aenter__(self) -> "HttpMemoryClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()
