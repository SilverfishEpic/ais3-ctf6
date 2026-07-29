"""The same-origin proxy and the page that rides on it.

Every ``/api/v1/*`` route is ``"/api"`` plus a path from the frozen contract, and every
handler is one call into :class:`MemoryCoreClient`. The proxy adds exactly three things and
declines everything else:

* it supplies ``actor`` from server configuration, so a page cannot claim to be somebody
  else;
* it rejects a challenge id that is not a name, because routing hands the handler an
  already-decoded path parameter and the HTTP client would then normalize ``..`` away
  before Memory Core ever saw it;
* it builds the ingest envelope with the same helpers ``ctfmem`` uses.

It validates no request body. A missing ``reason``, a non-integer revision and an unknown
operation are all answered by Memory Core in Memory Core's own words, because a proxy that
re-validated a frozen schema would be a second, divergent authority on it.

Responses are forwarded unmodified: original status, original body. Errors the proxy itself
originates use a ``webui_error`` envelope so that nothing the browser receives can be
mistaken for the service's, and they avoid the status codes the contract's error table
already spends.

Handlers are intentionally thin and do not perform domain validation. The HTTP client is
currently synchronous; if the web UI grows beyond the local single-operator cockpit use
case, this should move to an async client rather than grow a second Memory Core authority
inside the web process.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from ..client import MemoryCoreClient
from ..config import ClientConfig
from ..errors import CoreConnectionError, CoreError
from ..models import CommandResult
from . import agent as web_agent
from .agent import AgentSettings, SpawnAgent, default_spawn

STATIC_DIR = Path(__file__).resolve().parent / "static"

CHALLENGE_ID_PATTERN = r"^[A-Za-z0-9_-][A-Za-z0-9._-]*$"

_CHALLENGE_ID_RE = re.compile(CHALLENGE_ID_PATTERN)


class LoopbackHostMiddleware:
    """Reject requests whose Host header is not the configured loopback origin.

    This is intentionally implemented as plain ASGI middleware instead of FastAPI's
    ``@app.middleware("http")`` helper. The helper uses Starlette's BaseHTTPMiddleware,
    which can deadlock in the in-process ASGI tests that drive synchronous route handlers.
    """

    def __init__(self, app: Any, *, allowed_hosts: frozenset[str], port: int) -> None:
        self.app = app
        self.allowed_hosts = allowed_hosts
        self.port = port

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        host = headers.get("host", "")
        if host not in self.allowed_hosts:
            response = _webui_error_response(
                403,
                "forbidden_host",
                f"host {host!r} is not this server; reach it as http://127.0.0.1:{self.port}",
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


class WebUiError(Exception):
    """An error this proxy originates, as opposed to one it forwards."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def create_web_app(
    *,
    client: MemoryCoreClient,
    summarizing_client: MemoryCoreClient,
    config: ClientConfig,
    port: int,
    agent: AgentSettings | None = None,
    spawn: SpawnAgent = default_spawn,
) -> FastAPI:
    """Build the app around an injected pair of clients.

    ``client`` serves the routes that only touch local persistence; ``summarizing_client``
    serves ingest, query and challenge creation, the three that can reach the Summarizer.
    Tests inject the same object for both.

    ``agent`` is ``None`` unless a workspace was named at launch, and while it is ``None``
    the agent routes refuse and the page hides the panel. ``spawn`` is the seam that lets a
    test assert what would have been launched without launching it.

    ``openapi_url=None`` also disables ``/docs`` and ``/redoc``. This server publishes no
    contract of its own next to the frozen one.
    """

    app = FastAPI(title="ctfmem web", version="0.1.0", openapi_url=None)
    allowed_hosts = frozenset(
        {f"127.0.0.1:{port}", f"localhost:{port}", "127.0.0.1", "localhost"}
    )
    app.add_middleware(LoopbackHostMiddleware, allowed_hosts=allowed_hosts, port=port)

    async def _core_error(request: Request, exc: Exception) -> Response:
        if not isinstance(exc, CoreError):
            raise exc
        return JSONResponse(status_code=exc.status_code, content=exc.body)

    async def _core_unreachable(request: Request, exc: Exception) -> Response:
        return _webui_error_response(
            503,
            "core_unreachable",
            f"cannot reach Memory Core at {config.api_url}: {exc}",
        )

    async def _webui_error(request: Request, exc: Exception) -> Response:
        if not isinstance(exc, WebUiError):
            raise exc
        return _webui_error_response(exc.status_code, exc.code, exc.message)

    app.add_exception_handler(CoreError, _core_error)
    app.add_exception_handler(CoreConnectionError, _core_unreachable)
    app.add_exception_handler(WebUiError, _webui_error)

    @app.get("/api/local/config")
    async def local_config() -> Any:
        return {
            "actor": config.actor,
            "api_url": config.api_url,
            "templates_source": f"{config.api_url.rstrip('/')}/v1/templates",
            "port": port,
        }

    @app.get("/api/local/templates")
    async def local_templates() -> Any:
        return client.list_templates()

    def _require_agent() -> AgentSettings:
        if agent is None:
            raise WebUiError(
                403,
                "agent_disabled",
                "agent runs are off; start ctfmem-web with --agent-workspace to enable them",
            )
        return agent

    @app.get("/api/local/agent/options")
    async def agent_options() -> Any:
        return web_agent.options(agent)

    @app.post("/api/local/agent/{challenge_id}/run")
    async def agent_run(challenge_id: str, body: dict[str, Any]) -> Any:
        _require_name(challenge_id)
        settings = _require_agent()
        if web_agent.is_running(settings, challenge_id):
            raise WebUiError(
                409,
                "agent_already_running",
                f"an agent is already running for {challenge_id}; stop it before starting another",
            )
        try:
            return web_agent.start(
                settings,
                body,
                api_url=config.api_url,
                actor=config.actor,
                challenge_id=challenge_id,
                spawn=spawn,
            )
        except ValueError as exc:
            raise WebUiError(400, "invalid_agent_request", str(exc)) from exc
        except OSError as exc:
            raise WebUiError(500, "agent_spawn_failed", str(exc)) from exc

    @app.get("/api/local/agent/{challenge_id}/status")
    async def agent_status(challenge_id: str) -> Any:
        _require_name(challenge_id)
        return web_agent.status(_require_agent(), challenge_id)

    @app.post("/api/local/agent/{challenge_id}/stop")
    async def agent_stop(challenge_id: str) -> Any:
        _require_name(challenge_id)
        return web_agent.stop(_require_agent(), challenge_id)

    @app.get("/api/healthz")
    async def healthz() -> Any:
        return client.healthz()

    @app.get("/api/v1/challenges")
    async def list_challenges() -> Any:
        return client.list_challenges()

    @app.get("/api/v1/templates")
    async def list_templates() -> Any:
        return client.list_templates()

    @app.post("/api/v1/challenges", status_code=201)
    async def create_challenge(body: dict[str, Any]) -> Any:
        return summarizing_client.create_challenge(
            _field(body,"id"),
            title=_field(body,"title"),
            template=_field(body,"template"),
            initial_input=_field(body,"initial_input"),
        )

    @app.get("/api/v1/challenges/{challenge_id}/memory")
    async def read_memory(
        challenge_id: str, layer: str | None = None, section: str | None = None
    ) -> Any:
        _require_name(challenge_id)
        return client.read_memory(challenge_id, layer=layer, section=section)

    @app.post("/api/v1/challenges/{challenge_id}/ingest")
    async def ingest(challenge_id: str, body: dict[str, Any]) -> Any:
        _require_name(challenge_id)
        kind = _field(body,"type")
        if kind == "note":
            return summarizing_client.ingest_note(
                challenge_id, _field(body,"content"), actor=config.actor
            )
        if kind == "command_result":
            result = CommandResult(
                command=_field(body,"command"),
                exit_code=_field(body,"exit_code"),
                stdout=_field(body,"stdout", ""),
                stderr=_field(body,"stderr", ""),
            )
            return summarizing_client.ingest_command_result(
                challenge_id, result, actor=config.actor
            )
        raise WebUiError(
            400,
            "unsupported_input_type",
            "this client submits 'note' and 'command_result'; agent run logs belong to the "
            "Agent Bridge",
        )

    @app.post("/api/v1/challenges/{challenge_id}/query")
    async def query(challenge_id: str, body: dict[str, Any]) -> Any:
        _require_name(challenge_id)
        return summarizing_client.query(
            challenge_id, _field(body,"question"), layers=_field(body,"layers")
        )

    @app.post("/api/v1/challenges/{challenge_id}/guess")
    async def guess(challenge_id: str, body: dict[str, Any]) -> Any:
        _require_name(challenge_id)
        return client.guess(
            challenge_id,
            count=_field(body,"count"),
            only_untried=_field(body,"only_untried"),
            seed=_field(body,"seed"),
        )

    @app.post("/api/v1/challenges/{challenge_id}/mutate")
    async def mutate(challenge_id: str, body: dict[str, Any]) -> Any:
        _require_name(challenge_id)
        return client.mutate(
            challenge_id,
            expected_revision=_field(body,"expected_revision"),
            actor=config.actor,
            reason=_field(body,"reason"),
            operations=_field(body,"operations"),
        )

    @app.post("/api/v1/challenges/{challenge_id}/template")
    async def set_template(challenge_id: str, body: dict[str, Any]) -> Any:
        _require_name(challenge_id)
        return client.set_template(
            challenge_id,
            template=_field(body,"template"),
            expected_revision=_field(body,"expected_revision"),
            actor=config.actor,
            reason=_field(body,"reason"),
        )

    @app.get("/openapi.json", include_in_schema=False)
    async def openapi_disabled() -> Response:
        return Response(status_code=404)

    @app.get("/", include_in_schema=False)
    async def index() -> Response:
        return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="page")

    return app


def _field(body: dict[str, Any], name: str, default: Any = None) -> Any:
    """Read one field of a request body without judging it.

    Typed as ``Any`` deliberately. Whatever the page sent is handed to the client method
    unchanged so that a missing ``reason``, a string where a revision belongs, or an
    unknown operation is answered by Memory Core in its own words. A proxy that rejected
    these itself would be a second authority on a frozen schema, and would answer the same
    mistake with a different status and a different envelope than the service does.
    """

    return body.get(name, default)


def _require_name(challenge_id: str) -> None:
    """Reject a challenge id that is a path rather than a name.

    Routing supplies an already-decoded path parameter, so ``%2E%2E`` arrives here as
    ``..``. Interpolated into a URL it would then be normalized away by the HTTP client and
    Memory Core would answer a question nobody asked. The pattern is duplicated from the
    contract rather than imported, because this package may not import the service.
    """

    if not _CHALLENGE_ID_RE.match(challenge_id):
        raise WebUiError(
            400,
            "invalid_challenge_id",
            f"challenge id {challenge_id!r} is not a name",
        )


def _webui_error_response(status_code: int, code: str, message: str) -> JSONResponse:
    """Errors this server authored, in an envelope the service's shape cannot be confused with."""

    return JSONResponse(
        status_code=status_code,
        content={"webui_error": {"code": code, "message": message}},
    )
