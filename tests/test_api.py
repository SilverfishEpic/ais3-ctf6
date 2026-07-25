"""HTTP surface: the contract Person 2 and Person 3 build against."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from conftest import agent_run_log, human_note
from fastapi.testclient import TestClient

from memory_core.api import create_app
from memory_core.service import MemoryService


@pytest.fixture
def client(service: MemoryService) -> Iterator[TestClient]:
    with TestClient(create_app(service)) as test_client:
        yield test_client


def _create(client: TestClient, challenge_id: str = "demo-web") -> None:
    response = client.post("/v1/challenges", json={"id": challenge_id, "title": "Demo Web"})
    assert response.status_code == 201, response.text


def test_healthz(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


def test_create_challenge_returns_revision_zero(client: TestClient) -> None:
    response = client.post("/v1/challenges", json={"id": "c1", "title": "C1"})
    assert response.status_code == 201
    assert response.json() == {
        "challenge_id": "c1",
        "revision": 0,
        "template": "generic",
        "template_version": 1,
    }


def test_duplicate_challenge_is_409(client: TestClient) -> None:
    _create(client, "c1")
    response = client.post("/v1/challenges", json={"id": "c1", "title": "C1"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "challenge_exists"


def test_list_challenges(client: TestClient) -> None:
    _create(client, "a")
    _create(client, "b")
    assert client.get("/v1/challenges").json() == ["a", "b"]


def test_ingest_returns_a_semantic_diff(client: TestClient) -> None:
    _create(client)
    response = client.post(
        "/v1/challenges/demo-web/ingest", json=human_note("I ran nmap and port 80 is open.")
    )
    assert response.status_code == 200
    body = response.json()
    assert body["revision_before"] == 0
    assert body["revision_after"] == 1
    assert body["diff"]["fact_added"] == ["F001"]
    assert body["diff"]["assumption_added"] == ["A001"]
    assert body["source_id"].startswith("I-")


def test_ingest_accepts_a_normalized_agent_run_log(client: TestClient) -> None:
    _create(client)
    response = client.post(
        "/v1/challenges/demo-web/ingest",
        json=agent_run_log(
            [
                {"seq": 1, "role": "assistant", "kind": "message", "content": "checking"},
                {"seq": 2, "role": "tool", "kind": "tool_result", "content": "NX enabled"},
            ]
        ),
    )
    assert response.status_code == 200
    assert response.json()["diff"]["fact_added"] == ["F001"]


def test_ingest_on_unknown_challenge_is_404(client: TestClient) -> None:
    response = client.post("/v1/challenges/nope/ingest", json=human_note("hi"))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "challenge_not_found"


def test_read_memory_supports_filters(client: TestClient) -> None:
    _create(client)
    client.post("/v1/challenges/demo-web/ingest", json=human_note("port 80 is open"))

    everything = client.get("/v1/challenges/demo-web/memory").json()
    assert everything["fact"]["target"][0]["id"] == "F001"
    assert everything["guessing"]["catalog"] == "generic@1"

    facts_only = client.get("/v1/challenges/demo-web/memory", params={"layer": "fact"}).json()
    assert facts_only["assumption"] == {}
    assert facts_only["guessing"] is None

    section = client.get(
        "/v1/challenges/demo-web/memory", params={"layer": "fact", "section": "target"}
    ).json()
    assert list(section["fact"]) == ["target"]


def test_unknown_layer_is_rejected_by_the_schema(client: TestClient) -> None:
    _create(client)
    response = client.get("/v1/challenges/demo-web/memory", params={"layer": "guessing2"})
    assert response.status_code == 422


def test_query_endpoint(client: TestClient) -> None:
    _create(client)
    client.post("/v1/challenges/demo-web/ingest", json=human_note("port 80 is open"))
    response = client.post(
        "/v1/challenges/demo-web/query", json={"question": "which ports are open?"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["references"] == ["F001"]
    assert body["revision"] == 1


def test_guess_endpoint_is_seeded(client: TestClient) -> None:
    _create(client)
    payload = {"count": 2, "only_untried": True, "seed": 1337}
    first = client.post("/v1/challenges/demo-web/guess", json=payload).json()
    second = client.post("/v1/challenges/demo-web/guess", json=payload).json()
    assert [c["id"] for c in first["candidates"]] == [c["id"] for c in second["candidates"]]
    assert first["catalog"] == "generic@1"


def test_mutate_endpoint(client: TestClient) -> None:
    _create(client)
    client.post("/v1/challenges/demo-web/ingest", json=human_note("port 80 is open"))
    response = client.post(
        "/v1/challenges/demo-web/mutate",
        json={
            "expected_revision": 1,
            "actor": "ctshen",
            "reason": "Verified manually",
            "operations": [{"op": "promote", "id": "A001", "to": "fact"}],
        },
    )
    assert response.status_code == 200
    assert response.json()["diff"]["promoted"] == ["A001"]


def test_stale_mutation_is_409_with_the_current_revision(client: TestClient) -> None:
    _create(client)
    client.post("/v1/challenges/demo-web/ingest", json=human_note("port 80 is open"))
    response = client.post(
        "/v1/challenges/demo-web/mutate",
        json={
            "expected_revision": 0,
            "actor": "ctshen",
            "reason": "stale client",
            "operations": [{"op": "promote", "id": "A001"}],
        },
    )
    assert response.status_code == 409
    body = response.json()["error"]
    assert body["code"] == "revision_conflict"
    assert body["details"]["current_revision"] == 1


def test_mutation_without_a_reason_is_rejected(client: TestClient) -> None:
    _create(client)
    response = client.post(
        "/v1/challenges/demo-web/mutate",
        json={
            "expected_revision": 0,
            "actor": "ctshen",
            "reason": "",
            "operations": [{"op": "add", "layer": "fact", "text": "x"}],
        },
    )
    assert response.status_code == 422


def test_unknown_memory_id_is_400(client: TestClient) -> None:
    _create(client)
    response = client.post(
        "/v1/challenges/demo-web/mutate",
        json={
            "expected_revision": 0,
            "actor": "ctshen",
            "reason": "typo",
            "operations": [{"op": "remove", "id": "F999"}],
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


def test_template_endpoint(client: TestClient) -> None:
    _create(client)
    response = client.post(
        "/v1/challenges/demo-web/template",
        json={
            "template": "web",
            "expected_revision": 0,
            "actor": "ctshen",
            "reason": "This is a web challenge.",
        },
    )
    assert response.status_code == 200
    assert response.json()["diff"]["template_changed"] == {"from": "generic@1", "to": "web@1"}


def test_unknown_template_is_404(client: TestClient) -> None:
    _create(client)
    response = client.post(
        "/v1/challenges/demo-web/template",
        json={
            "template": "quantum",
            "expected_revision": 0,
            "actor": "ctshen",
            "reason": "why not",
        },
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "template_not_found"


def test_openapi_document_exposes_the_frozen_endpoints(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert {
        "/v1/challenges",
        "/v1/challenges/{challenge_id}/ingest",
        "/v1/challenges/{challenge_id}/memory",
        "/v1/challenges/{challenge_id}/query",
        "/v1/challenges/{challenge_id}/guess",
        "/v1/challenges/{challenge_id}/mutate",
        "/v1/challenges/{challenge_id}/template",
    } <= set(paths)
