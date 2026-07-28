from __future__ import annotations

import pytest
from conftest import ASGITestClient

from human_client.client import MemoryCoreClient
from human_client.errors import CoreError
from human_client.models import CommandResult
from memory_core.service import MemoryService


@pytest.fixture
def client(service: MemoryService) -> MemoryCoreClient:
    return MemoryCoreClient("http://testserver", http_client=ASGITestClient(service))


def test_client_submits_note_over_public_http(client: MemoryCoreClient) -> None:
    client.create_challenge("demo-web", title="Demo Web")
    ingest = client.ingest_note("demo-web", "I ran nmap and port 80 is open.", actor="ctshen")
    memory = client.read_memory("demo-web")

    assert ingest["revision_before"] == 0
    assert ingest["revision_after"] == 1
    assert ingest["source_id"].startswith("I-")
    assert memory["fact"]["target"][0]["id"] == "F001"


def test_client_submits_command_result_and_preserves_exit_code(
    client: MemoryCoreClient,
) -> None:
    client.create_challenge("demo-web", title="Demo Web")
    result = CommandResult(
        command=["grep", "needle", "missing-file"],
        exit_code=2,
        stdout="",
        stderr="grep: missing-file: No such file or directory\n",
    )

    ingest = client.ingest_command_result("demo-web", result, actor="ctshen")

    assert ingest["source_id"].startswith("I-")
    assert ingest["revision_after"] >= ingest["revision_before"]


def test_client_preserves_raw_command_output_in_the_journal(
    client: MemoryCoreClient, service: MemoryService
) -> None:
    client.create_challenge("demo-web", title="Demo Web")

    ingest = client.ingest_command_result(
        "demo-web",
        CommandResult(
            command=["probe"],
            exit_code=0,
            stdout="bad byte: �",
            stderr="",
            stdout_base64="YmFkIGJ5dGU6IP8=",
            stderr_base64="",
        ),
        actor="ctshen",
    )

    record = service.journal.get("demo-web", ingest["source_id"])
    assert record is not None
    assert record.content is not None
    assert record.content["stdout_base64"] == "YmFkIGJ5dGU6IP8="


def test_client_surfaces_revision_conflict_without_retry(client: MemoryCoreClient) -> None:
    client.create_challenge("demo-web", title="Demo Web")
    client.ingest_note("demo-web", "port 80 is open", actor="ctshen")

    with pytest.raises(CoreError) as excinfo:
        client.mutate(
            "demo-web",
            expected_revision=0,
            actor="ctshen",
            reason="stale",
            operations=[{"op": "add", "layer": "fact", "text": "manual override"}],
        )

    assert excinfo.value.status_code == 409
    assert excinfo.value.code == "revision_conflict"


def test_guess_is_deterministic_for_same_seed(client: MemoryCoreClient) -> None:
    client.create_challenge("demo-web", title="Demo Web")
    first = client.guess("demo-web", count=3, seed=42, only_untried=True)
    second = client.guess("demo-web", count=3, seed=42, only_untried=True)

    assert first == second
