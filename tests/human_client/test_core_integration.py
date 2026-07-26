from __future__ import annotations

from conftest import ASGITestClient

from human_client.client import MemoryCoreClient
from memory_core.service import MemoryService


def test_fake_core_e2e_restart_persistence(service: MemoryService) -> None:
    client = MemoryCoreClient("http://testserver", http_client=ASGITestClient(service))
    client.create_challenge("demo-web", title="Demo Web")
    note = client.ingest_note("demo-web", "I ran nmap and port 80 is open.", actor="ctshen")
    before_restart = client.read_memory("demo-web")

    assert (note["revision_before"], note["revision_after"]) == (0, 1)
    assert before_restart["fact"]["target"][0]["id"] == "F001"

    reloaded = MemoryCoreClient("http://testserver", http_client=ASGITestClient(service))
    after_restart = reloaded.read_memory("demo-web")

    assert after_restart == before_restart
