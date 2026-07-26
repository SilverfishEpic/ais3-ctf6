"""HTTP surface: the contract Person 2 and Person 3 build against."""

from __future__ import annotations

from pathlib import Path

from conftest import ASGITestClient, agent_run_log, human_note


def _create(client: ASGITestClient, challenge_id: str = "demo-web") -> None:
    response = client.post("/v1/challenges", json={"id": challenge_id, "title": "Demo Web"})
    assert response.status_code == 201, response.text


def test_healthz(asgi_client: ASGITestClient) -> None:
    assert asgi_client.get("/healthz").json() == {"status": "ok"}


def test_create_challenge_returns_revision_zero(asgi_client: ASGITestClient) -> None:
    response = asgi_client.post("/v1/challenges", json={"id": "c1", "title": "C1"})
    assert response.status_code == 201
    assert response.json() == {
        "challenge_id": "c1",
        "revision": 0,
        "template": "generic",
        "template_version": 1,
    }


def test_duplicate_challenge_is_409(asgi_client: ASGITestClient) -> None:
    _create(asgi_client, "c1")
    response = asgi_client.post("/v1/challenges", json={"id": "c1", "title": "C1"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "challenge_exists"


def test_list_challenges(asgi_client: ASGITestClient) -> None:
    _create(asgi_client, "a")
    _create(asgi_client, "b")
    assert asgi_client.get("/v1/challenges").json() == ["a", "b"]


def test_ingest_returns_a_semantic_diff(asgi_client: ASGITestClient) -> None:
    _create(asgi_client)
    response = asgi_client.post(
        "/v1/challenges/demo-web/ingest", json=human_note("I ran nmap and port 80 is open.")
    )
    assert response.status_code == 200
    body = response.json()
    assert body["revision_before"] == 0
    assert body["revision_after"] == 1
    assert body["diff"]["fact_added"] == ["F001"]
    assert body["diff"]["assumption_added"] == ["A001"]
    assert body["source_id"].startswith("I-")


def test_ingest_accepts_a_normalized_agent_run_log(asgi_client: ASGITestClient) -> None:
    _create(asgi_client)
    response = asgi_client.post(
        "/v1/challenges/demo-web/ingest",
        json=agent_run_log(
            [
                {"seq": 1, "role": "assistant", "kind": "message", "content": "checking"},
                {
                    "seq": 2,
                    "role": "tool",
                    "kind": "tool_result",
                    "content": "NX enabled",
                    "outcome": "succeeded",
                },
            ]
        ),
    )
    assert response.status_code == 200
    assert response.json()["diff"]["fact_added"] == ["F001"]


def test_ingest_on_unknown_challenge_is_404(asgi_client: ASGITestClient) -> None:
    response = asgi_client.post("/v1/challenges/nope/ingest", json=human_note("hi"))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "challenge_not_found"


def test_a_path_shaped_challenge_id_is_refused(
    asgi_client: ASGITestClient, runs_dir: Path
) -> None:
    """``..`` must not create a challenge outside ``runs/``.

    It used to return 201 and write ``state.yaml`` into the parent of the runs directory,
    where ``GET /v1/challenges`` could never see it again.
    """

    response = asgi_client.post("/v1/challenges", json={"id": "..", "title": "escape"})
    assert response.status_code == 422, response.text
    assert asgi_client.post("/v1/challenges", json={"id": ".", "title": "dot"}).status_code == 422

    assert asgi_client.get("/v1/challenges").json() == []
    assert not (runs_dir.parent / "state.yaml").exists()
    assert list(runs_dir.iterdir()) == []


def test_a_path_shaped_id_in_the_url_is_400_not_a_filesystem_read(
    asgi_client: ASGITestClient,
) -> None:
    """The same rule on the path parameter, which request-body validation never sees.

    Percent-encoded so the HTTP client cannot helpfully normalize the ``..`` away before
    it is sent — the server has to be the one that refuses it.
    """

    response = asgi_client.get("/v1/challenges/%2E%2E/memory")
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "invalid_request"


def test_read_memory_supports_filters(asgi_client: ASGITestClient) -> None:
    _create(asgi_client)
    asgi_client.post("/v1/challenges/demo-web/ingest", json=human_note("port 80 is open"))

    everything = asgi_client.get("/v1/challenges/demo-web/memory").json()
    assert everything["fact"]["target"][0]["id"] == "F001"
    assert everything["guessing"]["catalog"] == "generic@1"

    facts_only = asgi_client.get(
        "/v1/challenges/demo-web/memory", params={"layer": "fact"}
    ).json()
    assert facts_only["assumption"] == {}
    assert facts_only["guessing"] is None

    section = asgi_client.get(
        "/v1/challenges/demo-web/memory", params={"layer": "fact", "section": "target"}
    ).json()
    assert list(section["fact"]) == ["target"]


def test_unknown_layer_is_rejected_by_the_schema(asgi_client: ASGITestClient) -> None:
    _create(asgi_client)
    response = asgi_client.get("/v1/challenges/demo-web/memory", params={"layer": "guessing2"})
    assert response.status_code == 422


def test_query_endpoint(asgi_client: ASGITestClient) -> None:
    _create(asgi_client)
    asgi_client.post("/v1/challenges/demo-web/ingest", json=human_note("port 80 is open"))
    response = asgi_client.post(
        "/v1/challenges/demo-web/query", json={"question": "which ports are open?"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["references"] == ["F001"]
    assert body["revision"] == 1


def test_guess_endpoint_is_seeded(asgi_client: ASGITestClient) -> None:
    _create(asgi_client)
    payload = {"count": 2, "only_untried": True, "seed": 1337}
    first = asgi_client.post("/v1/challenges/demo-web/guess", json=payload).json()
    second = asgi_client.post("/v1/challenges/demo-web/guess", json=payload).json()
    assert [c["id"] for c in first["candidates"]] == [c["id"] for c in second["candidates"]]
    assert first["catalog"] == "generic@1"


def test_mutate_endpoint(asgi_client: ASGITestClient) -> None:
    _create(asgi_client)
    asgi_client.post("/v1/challenges/demo-web/ingest", json=human_note("port 80 is open"))
    response = asgi_client.post(
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


def test_stale_mutation_is_409_with_the_current_revision(asgi_client: ASGITestClient) -> None:
    _create(asgi_client)
    asgi_client.post("/v1/challenges/demo-web/ingest", json=human_note("port 80 is open"))
    response = asgi_client.post(
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


def test_mutation_without_a_reason_is_rejected(asgi_client: ASGITestClient) -> None:
    _create(asgi_client)
    response = asgi_client.post(
        "/v1/challenges/demo-web/mutate",
        json={
            "expected_revision": 0,
            "actor": "ctshen",
            "reason": "",
            "operations": [{"op": "add", "layer": "fact", "text": "x"}],
        },
    )
    assert response.status_code == 422


def test_unknown_memory_id_is_400(asgi_client: ASGITestClient) -> None:
    _create(asgi_client)
    response = asgi_client.post(
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


def test_template_endpoint(asgi_client: ASGITestClient) -> None:
    _create(asgi_client)
    response = asgi_client.post(
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


def test_unknown_template_is_404(asgi_client: ASGITestClient) -> None:
    _create(asgi_client)
    response = asgi_client.post(
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


def test_openapi_document_exposes_the_frozen_endpoints(asgi_client: ASGITestClient) -> None:
    paths = asgi_client.get("/openapi.json").json()["paths"]
    assert {
        "/v1/challenges",
        "/v1/challenges/{challenge_id}/ingest",
        "/v1/challenges/{challenge_id}/memory",
        "/v1/challenges/{challenge_id}/query",
        "/v1/challenges/{challenge_id}/guess",
        "/v1/challenges/{challenge_id}/mutate",
        "/v1/challenges/{challenge_id}/template",
    } <= set(paths)
