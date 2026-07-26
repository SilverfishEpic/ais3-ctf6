from __future__ import annotations

from typing import Any, Protocol, cast

import httpx


class MemoryCoreHTTPError(RuntimeError):
    def __init__(self, status_code: int, body: dict[str, Any] | str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"Memory Core returned HTTP {status_code}: {body}")

    @property
    def code(self) -> str | None:
        if isinstance(self.body, dict):
            error = self.body.get("error")
            if isinstance(error, dict):
                code = error.get("code")
                return code if isinstance(code, str) else None
        return None


class _HTTPClient(Protocol):
    def get(self, url: str, **kwargs: Any) -> Any: ...

    def post(self, url: str, **kwargs: Any) -> Any: ...


class MemoryCoreClient:
    """Human Client adapter for the public Memory Core HTTP contract."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        *,
        http_client: _HTTPClient | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = http_client or httpx.Client(base_url=self.base_url, timeout=timeout)

    def create_challenge(
        self,
        challenge_id: str,
        title: str,
        *,
        template: str | None = None,
        initial_input: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"id": challenge_id, "title": title}
        if template is not None:
            payload["template"] = template
        if initial_input is not None:
            payload["initial_input"] = initial_input
        return self._dict(self._json(self._client.post("/v1/challenges", json=payload), {201}))

    def list_challenges(self) -> list[str]:
        body = self._json(self._client.get("/v1/challenges"), {200})
        return cast(list[str], body)

    def ingest_human_note(self, challenge_id: str, content: str, *, actor: str) -> dict[str, Any]:
        return self._ingest(
            challenge_id,
            {
                "source": {"type": "human", "actor": actor},
                "input": {"type": "note", "content": content},
            },
        )

    def ingest_command_result(
        self,
        challenge_id: str,
        command: list[str],
        *,
        exit_code: int,
        stdout: str = "",
        stderr: str = "",
        actor: str,
    ) -> dict[str, Any]:
        return self._ingest(
            challenge_id,
            {
                "source": {"type": "human", "actor": actor},
                "input": {
                    "type": "command_result",
                    "command": command,
                    "exit_code": exit_code,
                    "stdout": stdout,
                    "stderr": stderr,
                },
            },
        )

    def read_memory(
        self, challenge_id: str, *, layer: str | None = None, section: str | None = None
    ) -> dict[str, Any]:
        params = {k: v for k, v in {"layer": layer, "section": section}.items() if v is not None}
        response = self._client.get(f"/v1/challenges/{challenge_id}/memory", params=params)
        return self._dict(self._json(response, {200}))

    def query(
        self, challenge_id: str, question: str, *, layers: list[str] | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"question": question}
        if layers is not None:
            payload["layers"] = layers
        response = self._client.post(f"/v1/challenges/{challenge_id}/query", json=payload)
        return self._dict(self._json(response, {200}))

    def guess(
        self,
        challenge_id: str,
        *,
        count: int = 3,
        only_untried: bool = True,
        seed: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"count": count, "only_untried": only_untried}
        if seed is not None:
            payload["seed"] = seed
        response = self._client.post(f"/v1/challenges/{challenge_id}/guess", json=payload)
        return self._dict(self._json(response, {200}))

    def mutate(
        self,
        challenge_id: str,
        *,
        expected_revision: int,
        actor: str,
        reason: str,
        operations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        response = self._client.post(
            f"/v1/challenges/{challenge_id}/mutate",
            json={
                "expected_revision": expected_revision,
                "actor": actor,
                "reason": reason,
                "operations": operations,
            },
        )
        return self._dict(self._json(response, {200}))

    def change_template(
        self,
        challenge_id: str,
        *,
        template: str,
        expected_revision: int,
        actor: str,
        reason: str,
    ) -> dict[str, Any]:
        response = self._client.post(
            f"/v1/challenges/{challenge_id}/template",
            json={
                "template": template,
                "expected_revision": expected_revision,
                "actor": actor,
                "reason": reason,
            },
        )
        return self._dict(self._json(response, {200}))

    def _ingest(self, challenge_id: str, envelope: dict[str, Any]) -> dict[str, Any]:
        response = self._client.post(f"/v1/challenges/{challenge_id}/ingest", json=envelope)
        return self._dict(self._json(response, {200}))

    @staticmethod
    def _dict(body: Any) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise TypeError(f"expected JSON object, got {type(body).__name__}")
        return body

    @staticmethod
    def _json(response: Any, expected: set[int]) -> Any:
        try:
            body = response.json()
        except ValueError:
            body = response.text
        if response.status_code not in expected:
            raise MemoryCoreHTTPError(response.status_code, body)
        return body
