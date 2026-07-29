from __future__ import annotations

import asyncio
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI

from human_client.config import ClientConfig
from human_client.errors import CoreError
from human_client.models import CommandResult, command_result_envelope, human_note_envelope
from human_client.web.app import create_web_app
from memory_core.errors import MemoryCoreError
from memory_core.models import (
    CreateChallengeRequest,
    IngestEnvelope,
    MemoryMutation,
    TemplateChangeRequest,
)
from memory_core.service import MemoryService

PORT = 8787
ORIGIN = f"http://127.0.0.1:{PORT}"


class DirectMemoryServiceClient:
    """Synchronous web-client double backed by the real MemoryService.

    The web app itself is already an ASGI app in these tests. Calling Memory Core through a
    second ASGITransport from inside that request would require nested event loops. This
    adapter keeps the test focused on the web proxy while still exercising the real service
    methods and error mapping.
    """

    def __init__(self, service: MemoryService) -> None:
        self.service = service

    def create_challenge(
        self,
        challenge_id: str,
        *,
        title: str,
        template: str | None = None,
        initial_input: str | None = None,
    ) -> dict[str, Any]:
        return self._call(
            self.service.create_challenge,
            CreateChallengeRequest(
                id=challenge_id,
                title=title,
                template=template,
                initial_input=initial_input,
            ),
        )

    def ingest_note(self, challenge_id: str, content: str, *, actor: str) -> dict[str, Any]:
        return self._call(
            self.service.ingest,
            challenge_id,
            IngestEnvelope.model_validate(human_note_envelope(content, actor)),
        )

    def ingest_command_result(
        self, challenge_id: str, result: CommandResult, *, actor: str
    ) -> dict[str, Any]:
        return self._call(
            self.service.ingest,
            challenge_id,
            IngestEnvelope.model_validate(command_result_envelope(result, actor)),
        )

    def read_memory(
        self, challenge_id: str, *, layer: str | None = None, section: str | None = None
    ) -> dict[str, Any]:
        return self._call(self.service.read_memory, challenge_id, layer=layer, section=section)

    def mutate(
        self,
        challenge_id: str,
        *,
        expected_revision: int,
        actor: str,
        reason: str,
        operations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self._call(
            self.service.mutate,
            challenge_id,
            MemoryMutation.model_validate(
                {
                    "expected_revision": expected_revision,
                    "actor": actor,
                    "reason": reason,
                    "operations": operations,
                }
            ),
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
        return self._call(
            self.service.set_template,
            challenge_id,
            TemplateChangeRequest(
                template=template,
                expected_revision=expected_revision,
                actor=actor,
                reason=reason,
            ),
        )

    def _call(self, func: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            result = func(*args, **kwargs)
        except MemoryCoreError as exc:
            raise CoreError(
                exc.status,
                {
                    "error": {
                        "code": exc.code,
                        "message": exc.message,
                        "details": exc.details,
                    }
                },
            ) from exc
        if hasattr(result, "model_dump"):
            return cast(dict[str, Any], result.model_dump(mode="json"))
        return cast(dict[str, Any], result)


def call(app: FastAPI, method: str, url: str, **kwargs: Any) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
            return await client.request(method, url, **kwargs)

    return asyncio.run(send())


@pytest.fixture
def app(service: MemoryService) -> FastAPI:
    """The proxy over a real Memory Core with the deterministic Summarizer.

    Everything else in this file's neighbourhood drives a recorder. This one exists so the
    suite can still notice when the proxy and the service stop agreeing.
    """

    client = DirectMemoryServiceClient(service)
    config = ClientConfig(
        api_url="http://testserver",
        challenge=None,
        actor="operator",
        timeout=30.0,
        json_output=False,
        verbose=False,
        color=False,
    )
    return create_web_app(
        client=client,  # type: ignore[arg-type]
        summarizing_client=client,  # type: ignore[arg-type]
        config=config,
        port=PORT,
    )


def test_the_whole_human_path_works_through_the_proxy(app: FastAPI) -> None:
    created = call(app, "POST", "/api/v1/challenges", json={"id": "demo-web", "title": "Demo Web"})
    assert created.status_code == 201

    ingested = call(
        app,
        "POST",
        "/api/v1/challenges/demo-web/ingest",
        json={"type": "note", "content": "I ran nmap and port 80 is open."},
    )
    assert ingested.status_code == 200
    assert (ingested.json()["revision_before"], ingested.json()["revision_after"]) == (0, 1)

    board = call(app, "GET", "/api/v1/challenges/demo-web/memory").json()
    assert board["revision"] == 1
    assert board["fact"]["target"][0]["id"] == "F001"
    assumption_id = next(
        item["id"] for items in board["assumption"].values() for item in items
    )

    promoted = call(
        app,
        "POST",
        "/api/v1/challenges/demo-web/mutate",
        json={
            "expected_revision": 1,
            "reason": "confirmed by hand",
            "operations": [{"op": "promote", "id": assumption_id, "to": "fact"}],
        },
    )
    assert promoted.status_code == 200
    assert promoted.json()["diff"]["promoted"] == [assumption_id]

    stale = call(
        app,
        "POST",
        "/api/v1/challenges/demo-web/mutate",
        json={
            "expected_revision": 1,
            "reason": "same revision twice",
            "operations": [{"op": "remove", "id": "F001"}],
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "revision_conflict"
    assert stale.json()["error"]["details"]["current_revision"] == 2


def test_the_write_is_attributed_to_the_server_actor_not_the_page(app: FastAPI) -> None:
    call(app, "POST", "/api/v1/challenges", json={"id": "demo-web", "title": "Demo Web"})
    call(
        app,
        "POST",
        "/api/v1/challenges/demo-web/ingest",
        json={"type": "note", "content": "I ran nmap and port 80 is open.", "actor": "intruder"},
    )

    board = call(app, "GET", "/api/v1/challenges/demo-web/memory").json()

    assert board["fact"]["target"][0]["source_actor"] == "operator"


def test_the_board_carries_the_section_layout_the_page_offers(app: FastAPI) -> None:
    """The section pickers are built from this response, so the layout has to be in it.

    Memory Core materializes every section a template declares precisely so a client does
    not need a second lookup. If that ever stops being true, the pickers silently shrink to
    whatever happens to be populated, and this is the test that says so.
    """

    call(app, "POST", "/api/v1/challenges", json={"id": "demo-web", "title": "Demo Web"})
    call(
        app,
        "POST",
        "/api/v1/challenges/demo-web/ingest",
        json={"type": "note", "content": "I ran nmap and port 80 is open."},
    )

    board = call(app, "GET", "/api/v1/challenges/demo-web/memory").json()
    populated = [name for name, items in board["fact"].items() if items]

    assert "general" in board["fact"]
    assert "general" in board["assumption"]
    assert len(board["fact"]) > len(populated)


def test_an_unknown_template_is_reported_by_the_core_not_guessed_at(app: FastAPI) -> None:
    call(app, "POST", "/api/v1/challenges", json={"id": "demo-web", "title": "Demo Web"})

    response = call(
        app,
        "POST",
        "/api/v1/challenges/demo-web/template",
        json={"template": "not-a-template", "expected_revision": 0, "reason": "typo"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "template_not_found"
