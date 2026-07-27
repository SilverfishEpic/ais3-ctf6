from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from conftest import REPO_ROOT
from fastapi import FastAPI

from human_client.config import ClientConfig
from human_client.errors import CoreConnectionError, CoreError
from human_client.web.app import create_web_app
from human_client.web.catalog import Catalog, GuessingLabel, TemplateSummary

PORT = 8787
ORIGIN = f"http://127.0.0.1:{PORT}"
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


class RecordingClient:
    """Duck-types the parts of the adapter the proxy uses, and records what it was asked.

    The proxy is the unit under test here. Driving it against a recorder keeps these tests
    about what the proxy forwards, and leaves what Memory Core does with it to the suites
    that own Memory Core.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.result: Any = {"ok": True}
        self.error: Exception | None = None

    def _record(self, name: str, **kwargs: Any) -> Any:
        self.calls.append((name, kwargs))
        if self.error is not None:
            raise self.error
        return self.result

    @property
    def last(self) -> tuple[str, dict[str, Any]]:
        return self.calls[-1]

    def healthz(self) -> Any:
        return self._record("healthz")

    def list_challenges(self) -> Any:
        return self._record("list_challenges")

    def create_challenge(self, challenge_id: Any, **kwargs: Any) -> Any:
        return self._record("create_challenge", challenge_id=challenge_id, **kwargs)

    def read_memory(self, challenge_id: Any, **kwargs: Any) -> Any:
        return self._record("read_memory", challenge_id=challenge_id, **kwargs)

    def ingest_note(self, challenge_id: Any, content: Any, **kwargs: Any) -> Any:
        return self._record("ingest_note", challenge_id=challenge_id, content=content, **kwargs)

    def ingest_command_result(self, challenge_id: Any, result: Any, **kwargs: Any) -> Any:
        return self._record(
            "ingest_command_result", challenge_id=challenge_id, result=result, **kwargs
        )

    def query(self, challenge_id: Any, question: Any, **kwargs: Any) -> Any:
        return self._record("query", challenge_id=challenge_id, question=question, **kwargs)

    def guess(self, challenge_id: Any, **kwargs: Any) -> Any:
        return self._record("guess", challenge_id=challenge_id, **kwargs)

    def mutate(self, challenge_id: Any, **kwargs: Any) -> Any:
        return self._record("mutate", challenge_id=challenge_id, **kwargs)

    def set_template(self, challenge_id: Any, **kwargs: Any) -> Any:
        return self._record("set_template", challenge_id=challenge_id, **kwargs)


def call(app: FastAPI, method: str, url: str, **kwargs: Any) -> httpx.Response:
    """Drive the app in process, the way ``conftest.ASGITestClient`` drives Memory Core.

    No socket is opened, so the suite's offline guarantee is never in question, and no
    blocking portal is started, so the synchronous handlers run in the transport's own
    threadpool rather than under a nested event loop.
    """

    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
            return await client.request(method, url, **kwargs)

    return asyncio.run(send())


@pytest.fixture
def core() -> RecordingClient:
    return RecordingClient()


@pytest.fixture
def catalog() -> Catalog:
    return Catalog(
        directory="/tmp/templates",
        templates=[
            TemplateSummary(
                id="web",
                version=1,
                description="Web security challenge",
                guessing=[GuessingLabel(id="WEB-SQLI", title="SQL Injection", tags=["database"])],
            )
        ],
        problems=[],
    )


@pytest.fixture
def config() -> ClientConfig:
    return ClientConfig(
        api_url="http://127.0.0.1:8777",
        challenge=None,
        actor="operator",
        timeout=30.0,
        json_output=False,
        verbose=False,
        color=False,
    )


@pytest.fixture
def app(core: RecordingClient, config: ClientConfig, catalog: Catalog) -> FastAPI:
    return create_web_app(
        client=core,  # type: ignore[arg-type]
        summarizing_client=core,  # type: ignore[arg-type]
        config=config,
        catalog=catalog,
        port=PORT,
    )


def contract_operations() -> set[tuple[str, str]]:
    document = yaml.safe_load((REPO_ROOT / "spec" / "openapi.yaml").read_text(encoding="utf-8"))
    return {
        (method.upper(), "/api" + path)
        for path, operations in document["paths"].items()
        for method in operations
        if method in HTTP_METHODS
    }


def test_the_proxy_mirrors_every_contract_operation_and_adds_none(app: FastAPI) -> None:
    proxied = {
        (method, route.path)
        for route in app.routes
        for method in getattr(route, "methods", set())
        if route.path.startswith("/api/") and not route.path.startswith("/api/local/")
    }

    assert proxied == contract_operations()


def test_local_routes_are_the_only_endpoints_that_are_not_a_proxy(app: FastAPI) -> None:
    local = {
        route.path
        for route in app.routes
        if route.path.startswith("/api/local/")
    }

    assert local == {
        "/api/local/config",
        "/api/local/templates",
        "/api/local/agent/options",
        "/api/local/agent/{challenge_id}/run",
        "/api/local/agent/{challenge_id}/status",
        "/api/local/agent/{challenge_id}/stop",
    }


def test_agent_routes_stay_out_of_the_contract_namespace(app: FastAPI) -> None:
    """An agent run is this client's business, not an operation Memory Core publishes."""

    contract_paths = [route.path for route in app.routes if route.path.startswith("/api/v1/")]

    assert not any("agent" in path for path in contract_paths)


def test_the_agent_is_off_unless_a_workspace_was_configured(app: FastAPI) -> None:
    options = call(app, "GET", "/api/local/agent/options").json()
    run = call(app, "POST", "/api/local/agent/demo/run", json={"prompt": "go"})

    assert options["enabled"] is False
    assert run.status_code == 403
    assert run.json()["webui_error"]["code"] == "agent_disabled"


def test_the_server_publishes_no_contract_of_its_own(app: FastAPI) -> None:
    assert app.openapi_url is None
    assert call(app, "GET", "/openapi.json").status_code == 404


def test_a_successful_body_is_forwarded_unmodified(app: FastAPI, core: RecordingClient) -> None:
    core.result = {"revision": 7, "challenge": {"id": "demo", "template": "web"}, "fact": {}}

    response = call(app, "GET", "/api/v1/challenges/demo/memory")

    assert response.status_code == 200
    assert response.json() == core.result


def test_creation_keeps_the_contract_status(app: FastAPI, core: RecordingClient) -> None:
    core.result = {"id": "demo", "revision": 0}

    response = call(app, "POST", "/api/v1/challenges", json={"id": "demo", "title": "Demo"})

    assert response.status_code == 201
    assert core.last[0] == "create_challenge"
    assert core.last[1]["challenge_id"] == "demo"
    assert core.last[1]["title"] == "Demo"


def test_a_core_error_reaches_the_browser_verbatim(app: FastAPI, core: RecordingClient) -> None:
    body = {
        "error": {
            "code": "revision_conflict",
            "message": "stale revision",
            "details": {"current_revision": 43},
        }
    }
    core.error = CoreError(409, body)

    response = call(
        app,
        "POST",
        "/api/v1/challenges/demo/mutate",
        json={"expected_revision": 41, "reason": "x", "operations": [{"op": "remove", "id": "F1"}]},
    )

    assert response.status_code == 409
    assert response.json() == body


def test_an_unreachable_core_is_not_dressed_up_as_a_core_error(
    app: FastAPI, core: RecordingClient
) -> None:
    core.error = CoreConnectionError("connection refused")

    response = call(app, "GET", "/api/v1/challenges")

    assert response.status_code == 503
    assert "error" not in response.json()
    assert response.json()["webui_error"]["code"] == "core_unreachable"


def test_a_challenge_id_that_is_a_path_never_reaches_the_core(
    app: FastAPI, core: RecordingClient
) -> None:
    response = call(app, "GET", "/api/v1/challenges/%2E%2E/memory")

    assert response.status_code == 400
    assert response.json()["webui_error"]["code"] == "invalid_challenge_id"
    assert core.calls == []


def test_a_wrong_host_header_is_refused(app: FastAPI) -> None:
    response = call(app, "GET", "/api/v1/challenges", headers={"Host": "attacker.example"})

    assert response.status_code == 403
    assert response.json()["webui_error"]["code"] == "forbidden_host"


def test_localhost_is_accepted_as_well_as_the_loopback_literal(app: FastAPI) -> None:
    response = call(app, "GET", "/api/v1/challenges", headers={"Host": f"localhost:{PORT}"})

    assert response.status_code == 200


def test_a_note_is_wrapped_in_the_envelope_with_the_server_actor(
    app: FastAPI, core: RecordingClient
) -> None:
    call(
        app,
        "POST",
        "/api/v1/challenges/demo/ingest",
        json={"type": "note", "content": "port 80 is open", "actor": "someone-else"},
    )

    name, kwargs = core.last
    assert name == "ingest_note"
    assert kwargs["content"] == "port 80 is open"
    assert kwargs["actor"] == "operator"


def test_a_command_result_keeps_its_exit_code(app: FastAPI, core: RecordingClient) -> None:
    call(
        app,
        "POST",
        "/api/v1/challenges/demo/ingest",
        json={
            "type": "command_result",
            "command": ["grep", "needle", "missing"],
            "exit_code": 2,
            "stderr": "no such file",
        },
    )

    name, kwargs = core.last
    assert name == "ingest_command_result"
    assert kwargs["result"]["exit_code"] == 2
    assert kwargs["result"]["stdout"] == ""
    assert kwargs["actor"] == "operator"


def test_an_agent_run_log_is_refused_by_this_client(app: FastAPI, core: RecordingClient) -> None:
    response = call(
        app, "POST", "/api/v1/challenges/demo/ingest", json={"type": "run_log", "events": []}
    )

    assert response.status_code == 400
    assert response.json()["webui_error"]["code"] == "unsupported_input_type"
    assert core.calls == []


def test_a_mutation_cannot_claim_a_different_actor(app: FastAPI, core: RecordingClient) -> None:
    call(
        app,
        "POST",
        "/api/v1/challenges/demo/mutate",
        json={
            "expected_revision": 3,
            "actor": "somebody-else",
            "reason": "verified in gdb",
            "operations": [{"op": "promote", "id": "A017", "to": "fact"}],
        },
    )

    name, kwargs = core.last
    assert name == "mutate"
    assert kwargs["actor"] == "operator"
    assert kwargs["expected_revision"] == 3
    assert kwargs["reason"] == "verified in gdb"
    assert kwargs["operations"] == [{"op": "promote", "id": "A017", "to": "fact"}]


def test_the_proxy_does_not_validate_a_body_it_does_not_own(
    app: FastAPI, core: RecordingClient
) -> None:
    call(app, "POST", "/api/v1/challenges/demo/mutate", json={"operations": []})

    name, kwargs = core.last
    assert name == "mutate"
    assert kwargs["expected_revision"] is None
    assert kwargs["reason"] is None


def test_guess_parameters_are_passed_through_without_restating_defaults(
    app: FastAPI, core: RecordingClient
) -> None:
    call(app, "POST", "/api/v1/challenges/demo/guess", json={"count": 50, "only_untried": False})

    name, kwargs = core.last
    assert name == "guess"
    assert kwargs["count"] == 50
    assert kwargs["only_untried"] is False
    assert kwargs["seed"] is None


def test_memory_filters_reach_the_core(app: FastAPI, core: RecordingClient) -> None:
    call(app, "GET", "/api/v1/challenges/demo/memory?layer=guessing&section=target")

    name, kwargs = core.last
    assert name == "read_memory"
    assert kwargs["layer"] == "guessing"
    assert kwargs["section"] == "target"


def test_local_config_reports_the_server_side_settings(app: FastAPI) -> None:
    body = call(app, "GET", "/api/local/config").json()

    assert body["actor"] == "operator"
    assert body["api_url"] == "http://127.0.0.1:8777"
    assert body["port"] == PORT


def test_local_templates_expose_titles_and_the_catalog_identifier(app: FastAPI) -> None:
    body = call(app, "GET", "/api/local/templates").json()

    assert body["templates"][0]["catalog"] == "web@1"
    assert body["templates"][0]["guessing"][0]["title"] == "SQL Injection"


def test_the_page_is_served_from_the_same_origin_as_the_api(app: FastAPI) -> None:
    response = call(app, "GET", "/")

    assert response.status_code == 200
    assert "ctfmem" in response.text


def test_the_page_pulls_in_nothing_from_the_network() -> None:
    static = Path(__file__).resolve().parents[2] / "human_client" / "web" / "static"
    markup = "\n".join(path.read_text(encoding="utf-8") for path in static.iterdir())

    assert "http://" not in markup.replace("http://127.0.0.1", "")
    assert "https://" not in markup
    assert "cdn" not in markup.lower()
