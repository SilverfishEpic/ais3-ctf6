from __future__ import annotations

from typing import Any, Protocol, cast

import httpx

from .errors import CoreConnectionError, CoreError
from .models import CommandResult, IngestEnvelope, command_result_envelope, human_note_envelope


class HTTPClient(Protocol):
    def get(self, url: str, **kwargs: Any) -> httpx.Response: ...

    def post(self, url: str, **kwargs: Any) -> httpx.Response: ...


class MemoryCoreClient:
    """Centralized HTTP adapter for the public Memory Core OpenAPI contract."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30.0,
        http_client: HTTPClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(base_url=self.base_url, timeout=timeout)

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if self._owns_client and callable(close):
            close()

    def create_challenge(
        self,
        challenge_id: str,
        *,
        title: str,
        template: str | None = None,
        initial_input: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"id": challenge_id, "title": title}
        if template is not None:
            payload["template"] = template
        if initial_input is not None:
            payload["initial_input"] = initial_input
        return self._object(self._request("POST", "/v1/challenges", json=payload, expected={201}))

    def list_challenges(self) -> list[str]:
        return cast(list[str], self._request("GET", "/v1/challenges", expected={200}))

    def ingest_note(self, challenge_id: str, content: str, *, actor: str) -> dict[str, Any]:
        return self.ingest(challenge_id, human_note_envelope(content, actor))

    def ingest_command_result(
        self, challenge_id: str, result: CommandResult, *, actor: str
    ) -> dict[str, Any]:
        return self.ingest(challenge_id, command_result_envelope(result, actor))

    def ingest(self, challenge_id: str, envelope: IngestEnvelope) -> dict[str, Any]:
        return self._object(
            self._request(
                "POST", f"/v1/challenges/{challenge_id}/ingest", json=envelope, expected={200}
            )
        )

    def read_memory(
        self, challenge_id: str, *, layer: str | None = None, section: str | None = None
    ) -> dict[str, Any]:
        params = {k: v for k, v in {"layer": layer, "section": section}.items() if v is not None}
        return self._object(
            self._request(
                "GET", f"/v1/challenges/{challenge_id}/memory", params=params, expected={200}
            )
        )

    def query(
        self, challenge_id: str, question: str, *, layers: list[str] | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"question": question}
        if layers is not None:
            payload["layers"] = layers
        return self._object(
            self._request(
                "POST", f"/v1/challenges/{challenge_id}/query", json=payload, expected={200}
            )
        )

    def guess(
        self,
        challenge_id: str,
        *,
        count: int,
        only_untried: bool,
        seed: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"count": count, "only_untried": only_untried}
        if seed is not None:
            payload["seed"] = seed
        return self._object(
            self._request(
                "POST", f"/v1/challenges/{challenge_id}/guess", json=payload, expected={200}
            )
        )

    def mutate(
        self,
        challenge_id: str,
        *,
        expected_revision: int,
        actor: str,
        reason: str,
        operations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self._object(
            self._request(
                "POST",
                f"/v1/challenges/{challenge_id}/mutate",
                json={
                    "expected_revision": expected_revision,
                    "actor": actor,
                    "reason": reason,
                    "operations": operations,
                },
                expected={200},
            )
        )

    def set_template(
        self,
        challenge_id: str,
        *,
        template: str,
        expected_revision: int,
        actor: str,
        reason: str,
    ) -> dict[str, Any]:
        return self._object(
            self._request(
                "POST",
                f"/v1/challenges/{challenge_id}/template",
                json={
                    "template": template,
                    "expected_revision": expected_revision,
                    "actor": actor,
                    "reason": reason,
                },
                expected={200},
            )
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        expected: set[int],
        **kwargs: Any,
    ) -> Any:
        try:
            response = (
                self._client.get(path, **kwargs)
                if method == "GET"
                else self._client.post(path, **kwargs)
            )
        except httpx.HTTPError as exc:
            raise CoreConnectionError(str(exc)) from exc
        try:
            body = response.json()
        except ValueError:
            body = response.text
        if response.status_code not in expected:
            raise CoreError(response.status_code, body)
        return body

    @staticmethod
    def _object(body: Any) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise TypeError(f"expected JSON object, got {type(body).__name__}")
        return body
