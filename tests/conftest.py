"""Shared fixtures, plus a hard guarantee that the default suite is offline.

The network guard is not decoration.  A memory system whose tests quietly depend on a
live model gives you no signal about your own logic and no way to run CI without a key —
so any accidental outbound connection fails loudly here.  The opt-in AIS3 check
(``-m live``) re-enables sockets for itself.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from memory_core.api import create_app
from memory_core.journal import Journal
from memory_core.locks import LockRegistry
from memory_core.service import MemoryService
from memory_core.store import StateStore
from memory_core.summarizer import FakeSummarizer
from memory_core.templates import TemplateRegistry

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = REPO_ROOT / "templates"


class NetworkBlocked(RuntimeError):
    pass


#: Captured at import, before any fixture has had a chance to replace them.
_REAL_CONNECT = socket.socket.connect
_REAL_CONNECT_EX = socket.socket.connect_ex
_REAL_SOCKETPAIR = socket.socketpair


class ASGITestClient:
    """Synchronous facade over httpx's in-process ASGI transport."""

    def __init__(self, service: MemoryService) -> None:
        self.app = create_app(service)

    def get(self, url: str, **kwargs: object) -> httpx.Response:
        return self._request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: object) -> httpx.Response:
        return self._request("POST", url, **kwargs)

    def _request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        async def send() -> httpx.Response:
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as test_client:
                return await test_client.request(method, url, **kwargs)

        return asyncio.run(send())


@pytest.fixture(autouse=True)
def _no_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker("live"):
        return

    # Every asyncio event loop wants a self-pipe.  On POSIX that is the AF_UNIX
    # ``socketpair`` syscall and never reaches ``connect``; on Windows CPython emulates it
    # with a real loopback listen/connect/accept, so an unqualified guard fails every
    # ``asyncio.run`` in the suite before the test body even starts.  The exemption below is
    # scoped to that one call, so a genuine outbound connect - loopback included - is still
    # blocked, and the guarantee in this module's docstring is unchanged.
    building_self_pipe = False

    def blocked(*_args: object, **_kwargs: object) -> None:
        raise NetworkBlocked(
            "the default test suite must not make network calls; "
            "mark the test with @pytest.mark.live if it genuinely needs one"
        )

    def guard(real: Callable[..., Any]) -> Callable[..., Any]:
        def check(*args: Any, **kwargs: Any) -> Any:
            if building_self_pipe:
                return real(*args, **kwargs)
            return blocked(*args, **kwargs)

        return check

    def socketpair(*args: Any, **kwargs: Any) -> tuple[socket.socket, socket.socket]:
        nonlocal building_self_pipe
        building_self_pipe = True
        try:
            return _REAL_SOCKETPAIR(*args, **kwargs)
        finally:
            building_self_pipe = False

    monkeypatch.setattr(socket.socket, "connect", guard(_REAL_CONNECT))
    monkeypatch.setattr(socket.socket, "connect_ex", guard(_REAL_CONNECT_EX))
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "socketpair", socketpair)


@pytest.fixture
def runs_dir(tmp_path: Path) -> Path:
    path = tmp_path / "runs"
    path.mkdir()
    return path


@pytest.fixture
def registry() -> TemplateRegistry:
    return TemplateRegistry.from_dir(TEMPLATES_DIR)


@pytest.fixture
def service(runs_dir: Path) -> MemoryService:
    return MemoryService.build(
        runs_dir=runs_dir, templates_dir=TEMPLATES_DIR, summarizer=FakeSummarizer()
    )


@pytest.fixture
def asgi_client(service: MemoryService) -> ASGITestClient:
    return ASGITestClient(service)


@pytest.fixture
def store(runs_dir: Path) -> StateStore:
    return StateStore(runs_dir, locks=LockRegistry())


@pytest.fixture
def journal(runs_dir: Path) -> Journal:
    return Journal(runs_dir, locks=LockRegistry())


@pytest.fixture
def demo(service: MemoryService) -> Iterator[str]:
    """A created-but-empty ``demo-web`` challenge at revision 0."""

    from memory_core.models import CreateChallengeRequest

    service.create_challenge(CreateChallengeRequest(id="demo-web", title="Demo Web"))
    yield "demo-web"


def human_note(content: str, actor: str = "ctshen") -> dict[str, object]:
    return {
        "source": {"type": "human", "actor": actor},
        "input": {"type": "note", "content": content},
    }


def command_result(
    command: list[str], stdout: str, actor: str = "ctshen", exit_code: int = 0
) -> dict[str, object]:
    return {
        "source": {"type": "human", "actor": actor},
        "input": {
            "type": "command_result",
            "command": command,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": "",
        },
    }


def agent_run_log(events: list[dict[str, object]], run_id: str = "run-123") -> dict[str, object]:
    return {
        "source": {
            "type": "agent",
            "provider": "anthropic",
            "model": "claude-x",
            "run_id": run_id,
            "session_id": "session-abc",
        },
        "input": {"type": "run_log", "events": events},
    }
