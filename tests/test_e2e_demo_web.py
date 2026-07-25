"""The agreed first integration fixture, end to end and fully offline.

This is the scenario from 共通設計起點 §10 that all three of us signed off on: if it
passes, the module boundaries hold.  It runs entirely over the HTTP surface, because that
is the only way Person 2 and Person 3 will ever touch Memory Core.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from conftest import ASGITestClient, agent_run_log, human_note

from memory_core.models import MemorySnapshot


def test_demo_web_end_to_end(asgi_client: ASGITestClient, runs_dir: Path) -> None:
    # 1. create the challenge
    created = asgi_client.post("/v1/challenges", json={"id": "demo-web", "title": "Demo Web"})
    assert created.status_code == 201
    assert created.json()["revision"] == 0

    # 2-4. a human reports an observation; F001 + A001 appear; revision 0 -> 1
    ingested = asgi_client.post(
        "/v1/challenges/demo-web/ingest", json=human_note("I ran nmap and port 80 is open.")
    ).json()
    assert (ingested["revision_before"], ingested["revision_after"]) == (0, 1)
    assert ingested["diff"]["fact_added"] == ["F001"]
    assert ingested["diff"]["assumption_added"] == ["A001"]

    # 5. the Human Client can render the board
    memory = asgi_client.get("/v1/challenges/demo-web/memory").json()
    assert memory["fact"]["target"][0] == {
        "id": "F001",
        "text": "TCP/80 is open.",
        "evidence": [ingested["source_id"]],
        "source_actor": "ctshen",
        "tags": [],
        "created_at": memory["fact"]["target"][0]["created_at"],
        "updated_at": memory["fact"]["target"][0]["updated_at"],
        "promoted_from": None,
    }
    assert memory["assumption"]["next_checks"][0]["text"] == (
        "The target may expose an HTTP application."
    )

    # 6. an agent reads memory before acting
    answer = asgi_client.post(
        "/v1/challenges/demo-web/query", json={"question": "which ports are open?"}
    ).json()
    assert answer["references"] == ["F001"]
    assert answer["revision"] == 1

    # 11. a seeded guess is reproducible
    guess_payload = {"count": 2, "only_untried": True, "seed": 42}
    first = asgi_client.post("/v1/challenges/demo-web/guess", json=guess_payload).json()
    second = asgi_client.post("/v1/challenges/demo-web/guess", json=guess_payload).json()
    assert [c["id"] for c in first["candidates"]] == [c["id"] for c in second["candidates"]]

    # 9. a human override, carrying the revision it was based on
    promoted = asgi_client.post(
        "/v1/challenges/demo-web/mutate",
        json={
            "expected_revision": 1,
            "actor": "ctshen",
            "reason": "Confirmed the HTTP banner in a browser",
            "operations": [{"op": "promote", "id": "A001", "to": "fact"}],
        },
    ).json()
    # Crossing layers mints a new id; `promoted` names the old one it came from.
    assert promoted["diff"]["promoted"] == ["A001"]
    assert promoted["diff"]["fact_added"] == ["F002"]
    assert promoted["revision_after"] == 2

    # 7-8. the agent returns a normalized run log; revision 2 -> 3
    agent_ingest = asgi_client.post(
        "/v1/challenges/demo-web/ingest",
        json=agent_run_log(
            [
                {
                    "seq": 1,
                    "role": "assistant",
                    "kind": "message",
                    "content": "checking protections",
                },
                {
                    "seq": 2,
                    "role": "assistant",
                    "kind": "tool_call",
                    "name": "Bash",
                    "content": "checksec ./chall",
                },
                {
                    "seq": 3,
                    "role": "tool",
                    "kind": "tool_result",
                    "content": "NX enabled",
                    "outcome": "succeeded",
                },
            ]
        ),
    ).json()
    assert (agent_ingest["revision_before"], agent_ingest["revision_after"]) == (2, 3)
    # F002 was consumed by the promotion above and is never reissued.
    assert agent_ingest["diff"]["fact_added"] == ["F003"]

    # 10. a stale revision is refused
    stale = asgi_client.post(
        "/v1/challenges/demo-web/mutate",
        json={
            "expected_revision": 1,
            "actor": "ctshen",
            "reason": "stale client",
            "operations": [{"op": "remove", "id": "F001"}],
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["details"]["current_revision"] == 3

    # reload from disk: state and journal are both still valid
    raw_state = yaml.safe_load((runs_dir / "demo-web" / "state.yaml").read_text(encoding="utf-8"))
    reloaded = MemorySnapshot.model_validate(raw_state)
    assert reloaded.revision == 3
    assert {i.id for _, i in reloaded.iter_facts()} == {"F001", "F002", "F003"}
    assert reloaded.iter_assumptions() == []
    assert reloaded.challenge.summary is not None
    # The id high-water marks are persisted, so a restart cannot reissue A001 or F002.
    assert (reloaded.meta.fact_seq, reloaded.meta.assumption_seq) == (3, 1)

    lines = [
        line for line in (runs_dir / "demo-web" / "journal.jsonl").read_text().splitlines() if line
    ]
    records = [json.loads(line) for line in lines]
    assert [r["type"] for r in records] == ["input", "mutation", "input"]
    # The raw human input survives untouched alongside the summary derived from it.
    assert records[0]["content"]["content"] == "I ran nmap and port 80 is open."

    # The promoted claim lives in the fact layer under a fact id, with a backlink to the
    # assumption it grew out of — so the F/A prefix always matches the current layer.
    promoted_item = next(i for _, i in reloaded.iter_facts() if i.promoted_from == "A001")
    assert promoted_item.id == "F002"
    assert promoted_item.text == "The target may expose an HTTP application."
