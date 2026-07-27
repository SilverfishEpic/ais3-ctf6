from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from conftest import TEMPLATES_DIR, ASGITestClient
from fastapi import FastAPI

from human_client.client import MemoryCoreClient
from human_client.config import ClientConfig
from human_client.web.app import create_web_app
from human_client.web.catalog import load_catalog
from memory_core.service import MemoryService

PORT = 8787
ORIGIN = f"http://127.0.0.1:{PORT}"


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

    client = MemoryCoreClient("http://testserver", http_client=ASGITestClient(service))
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
        client=client,
        summarizing_client=client,
        config=config,
        catalog=load_catalog(TEMPLATES_DIR),
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
