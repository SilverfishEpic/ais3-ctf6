"""Opt-in live check against the AIS3 backend.

Excluded from the default run by ``addopts = -m 'not live'``.  Nothing in CI, and nothing
in a normal ``pytest`` invocation, will touch the network.

    AIS3_BASE_URL=https://llm-api.zoolab.org/v1 \\
    AIS3_API_KEY=... \\
    AIS3_SUMMARIZER_MODEL=ais3/nemotron-3-ultra-550b \\
    AIS3_LIVE_TEST=1 \\
    pytest -m live -v
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from conftest import TEMPLATES_DIR, command_result

from memory_core.config import Settings
from memory_core.models import CreateChallengeRequest, IngestEnvelope
from memory_core.providers.ais3 import AIS3Summarizer
from memory_core.service import MemoryService

pytestmark = pytest.mark.live


@pytest.fixture
def live_service(runs_dir: Path) -> MemoryService:
    if os.environ.get("AIS3_LIVE_TEST") != "1":
        pytest.skip("set AIS3_LIVE_TEST=1 to run the live AIS3 check")
    settings = Settings.from_env()
    if not settings.ais3_api_key:
        pytest.skip("AIS3_API_KEY is not set")
    return MemoryService.build(
        runs_dir=runs_dir,
        templates_dir=TEMPLATES_DIR,
        summarizer=AIS3Summarizer(
            base_url=settings.ais3_base_url,
            api_key=settings.ais3_api_key,
            model=settings.ais3_model,
        ),
    )


def test_live_ingest_produces_a_validated_proposal(live_service: MemoryService) -> None:
    live_service.create_challenge(CreateChallengeRequest(id="live", title="Live check"))
    response = live_service.ingest(
        "live",
        IngestEnvelope.model_validate(
            command_result(
                ["nmap", "-sV", "10.0.0.5"],
                "PORT   STATE SERVICE VERSION\n80/tcp open  http    nginx 1.24.0\n",
            )
        ),
    )
    assert response.revision_after >= response.revision_before
    memory = live_service.read_memory("live")
    items = [i for items in memory.fact.values() for i in items] + [
        i for items in memory.assumption.values() for i in items
    ]
    assert items, "the model produced nothing at all from a clear nmap result"
    # Whatever the model said, the epistemic invariant still holds.
    for _, fact in live_service.store.load("live").iter_facts():
        assert fact.evidence, f"fact {fact.id} was committed without citing evidence"


def test_live_classification_returns_a_known_template(live_service: MemoryService) -> None:
    live_service.create_challenge(
        CreateChallengeRequest(
            id="live-web",
            title="Live web",
            initial_input="A login page at http://target/login sets a session cookie.",
        )
    )
    assert live_service.read_memory("live-web").challenge.template in {"generic", "web"}
