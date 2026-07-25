"""HTTP layer.

Deliberately thin: parse, delegate, serialise.  No business rule lives here — every
handler is a few lines, and the domain errors raised by the service decide the status
codes.  That is what lets ``tests/`` exercise the same semantics with and without a
server, and it keeps the OpenAPI document an honest projection of the domain types.

Handlers are plain ``def`` rather than ``async def`` on purpose: FastAPI runs them in a
threadpool, which is where the store's blocking per-challenge locks belong.  An ``async
def`` handler would block the event loop during a commit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse

from .config import Settings
from .errors import MemoryCoreError
from .models import (
    CreateChallengeRequest,
    CreateChallengeResponse,
    DiffEnvelope,
    ErrorResponse,
    GuessRequest,
    GuessResponse,
    IngestEnvelope,
    IngestResponse,
    MemoryMutation,
    MemoryReadResponse,
    QueryRequest,
    QueryResponse,
    TemplateChangeRequest,
)
from .service import MemoryService
from .summarizer import FakeSummarizer, Summarizer

DESCRIPTION = """\
Memory Core is a shared CTF whiteboard. Humans and external agents are clients; the only
model-driven component inside the service is a Summarizer.

Three memory concepts exist and no more:

* **Fact** - confirmed by direct evidence or an explicit human override.
* **Assumption** - plausible and testable, not yet verified.
* **Guessing** - a curated technique catalog owned by the challenge's template.

Memory Core is the only writer of `state.yaml`. Clients never edit it directly. All
state-changing operations return a machine-readable semantic diff, and all explicit
mutations carry `expected_revision` and fail with **409 Conflict** when stale.
"""

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse, "description": "Malformed or semantically invalid request"},
    404: {"model": ErrorResponse, "description": "Challenge or template not found"},
    409: {"model": ErrorResponse, "description": "Stale expected_revision, or challenge exists"},
    422: {"model": ErrorResponse, "description": "Summarizer proposal failed validation"},
    500: {"model": ErrorResponse, "description": "Server misconfigured, e.g. a rejected AIS3 key"},
    502: {"model": ErrorResponse, "description": "Summarizer backend unavailable or too slow"},
}


def get_service(request: Request) -> MemoryService:
    service: MemoryService | None = getattr(request.app.state, "service", None)
    if service is None:  # pragma: no cover - misconfiguration guard
        raise RuntimeError("app.state.service was never set; use create_app()")
    return service


ServiceDep = Annotated[MemoryService, Depends(get_service)]


def create_app(service: MemoryService) -> FastAPI:
    app = FastAPI(
        title="CTF Memory Core",
        version="2.0.0",
        description=DESCRIPTION,
        openapi_url="/openapi.json",
    )
    app.state.service = service

    @app.exception_handler(MemoryCoreError)
    def _domain_error(_: Request, exc: MemoryCoreError) -> JSONResponse:
        body = ErrorResponse.model_validate(
            {"error": {"code": exc.code, "message": exc.message, "details": exc.details or None}}
        )
        return JSONResponse(status_code=exc.status, content=body.model_dump(mode="json"))

    @app.get("/healthz", tags=["meta"], summary="Liveness probe")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        "/v1/challenges",
        response_model=CreateChallengeResponse,
        status_code=201,
        responses=ERROR_RESPONSES,
        tags=["challenges"],
        summary="Create a challenge at revision 0",
    )
    def create_challenge(
        payload: CreateChallengeRequest, service: ServiceDep
    ) -> CreateChallengeResponse:
        return service.create_challenge(payload)

    @app.get(
        "/v1/challenges",
        response_model=list[str],
        tags=["challenges"],
        summary="List known challenge ids",
    )
    def list_challenges(service: ServiceDep) -> list[str]:
        return service.list_challenges()

    @app.post(
        "/v1/challenges/{challenge_id}/ingest",
        response_model=IngestResponse,
        responses=ERROR_RESPONSES,
        tags=["memory"],
        summary="Ingest a normalized human or agent input",
        description=(
            "The envelope is vendor-neutral. Provider-specific events (e.g. Claude SDK "
            "objects) must be normalized by the Agent Bridge before they get here. The raw "
            "input is journalled before summarization, so nothing is lost if the Summarizer "
            "fails."
        ),
    )
    def ingest(challenge_id: str, payload: IngestEnvelope, service: ServiceDep) -> IngestResponse:
        return service.ingest(challenge_id, payload)

    @app.get(
        "/v1/challenges/{challenge_id}/memory",
        response_model=MemoryReadResponse,
        responses=ERROR_RESPONSES,
        tags=["memory"],
        summary="Read the whiteboard with deterministic filters",
    )
    def read_memory(
        challenge_id: str,
        service: ServiceDep,
        layer: Annotated[Literal["fact", "assumption", "guessing"] | None, Query()] = None,
        section: Annotated[str | None, Query()] = None,
    ) -> MemoryReadResponse:
        return service.read_memory(challenge_id, layer=layer, section=section)

    @app.post(
        "/v1/challenges/{challenge_id}/query",
        response_model=QueryResponse,
        responses=ERROR_RESPONSES,
        tags=["memory"],
        summary="Ask a natural-language question about current memory",
        description=(
            "Answers are grounded strictly in current whiteboard state. Returned references "
            "are intersected with the live snapshot, so an invented id cannot reach a client."
        ),
    )
    def query(challenge_id: str, payload: QueryRequest, service: ServiceDep) -> QueryResponse:
        return service.query(challenge_id, payload)

    @app.post(
        "/v1/challenges/{challenge_id}/guess",
        response_model=GuessResponse,
        responses=ERROR_RESPONSES,
        tags=["memory"],
        summary="Draw techniques from the template catalog",
        description=(
            "Deterministic given a seed, and never model-driven. Guessing is a curated "
            "procedural prior owned by the template, not LLM brainstorming."
        ),
    )
    def guess(challenge_id: str, payload: GuessRequest, service: ServiceDep) -> GuessResponse:
        return service.guess(challenge_id, payload)

    @app.post(
        "/v1/challenges/{challenge_id}/mutate",
        response_model=DiffEnvelope,
        responses=ERROR_RESPONSES,
        tags=["memory"],
        summary="Apply controlled human mutations",
        description=(
            "Controlled operations only - add / replace / remove / promote / demote / move / "
            "mark_tried. There is no arbitrary YAML patch endpoint. `expected_revision` is "
            "mandatory and a stale value returns 409."
        ),
    )
    def mutate(challenge_id: str, payload: MemoryMutation, service: ServiceDep) -> DiffEnvelope:
        return service.mutate(challenge_id, payload)

    @app.post(
        "/v1/challenges/{challenge_id}/template",
        response_model=DiffEnvelope,
        responses=ERROR_RESPONSES,
        tags=["memory"],
        summary="Explicitly reclassify the challenge",
        description=(
            "Reclassification re-lays-out the whiteboard. It preserves every memory id and "
            "every epistemic level: changing the category is not the same as re-judging what "
            "is true."
        ),
    )
    def set_template(
        challenge_id: str, payload: TemplateChangeRequest, service: ServiceDep
    ) -> DiffEnvelope:
        return service.set_template(challenge_id, payload)

    return app


def build_app(
    *,
    runs_dir: Path | None = None,
    templates_dir: Path | None = None,
    summarizer: Summarizer | None = None,
) -> FastAPI:
    """Convenience wiring used by ``__main__`` and by the spec generator."""

    settings = Settings.from_env()
    service = MemoryService.build(
        runs_dir=runs_dir or settings.runs_dir,
        templates_dir=templates_dir or settings.templates_dir,
        summarizer=summarizer or FakeSummarizer(),
    )
    return create_app(service)
