from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from core.human_cli.client import MemoryCoreClient, MemoryCoreHTTPError
from memory_core.api import create_app
from memory_core.service import MemoryService


@pytest.fixture
def human_client(service: MemoryService) -> MemoryCoreClient:
    with TestClient(create_app(service)) as http:
        yield MemoryCoreClient(http_client=http)


def test_human_client_uses_public_http_for_note_ingest(
    human_client: MemoryCoreClient,
) -> None:
    human_client.create_challenge("demo-web", "Demo Web")

    result = human_client.ingest_human_note(
        "demo-web", "I ran nmap and port 80 is open.", actor="ctshen"
    )
    memory = human_client.read_memory("demo-web")

    assert result["revision_before"] == 0
    assert result["revision_after"] == 1
    assert result["source_id"].startswith("I-")
    assert memory["revision"] == 1
    assert memory["fact"]["target"][0]["id"] == "F001"


def test_human_client_command_result_and_revision_conflict(
    human_client: MemoryCoreClient,
) -> None:
    human_client.create_challenge("demo-web", "Demo Web")
    human_client.ingest_command_result(
        "demo-web",
        ["nmap", "-sV", "127.0.0.1"],
        exit_code=0,
        stdout="80/tcp open http nginx",
        actor="ctshen",
    )

    with pytest.raises(MemoryCoreHTTPError) as excinfo:
        human_client.mutate(
            "demo-web",
            expected_revision=0,
            actor="ctshen",
            reason="stale",
            operations=[{"op": "add", "layer": "fact", "text": "manual override"}],
        )

    assert excinfo.value.status_code == 409
    assert excinfo.value.code == "revision_conflict"


def test_human_client_guess_template_and_query_paths(
    human_client: MemoryCoreClient,
) -> None:
    human_client.create_challenge("demo-web", "Demo Web")
    template = human_client.change_template(
        "demo-web",
        template="web",
        expected_revision=0,
        actor="ctshen",
        reason="web challenge",
    )
    guesses = human_client.guess("demo-web", count=2, seed=7)
    query = human_client.query("demo-web", "what do we know?")

    assert template["diff"]["template_changed"] == {"from": "generic@1", "to": "web@1"}
    assert guesses["catalog"] == "web@1"
    assert query["revision"] == 1
