"""The default suite must never touch the network, and the AIS3 provider must be
exercisable without it.

Two separate claims:

* the guard in ``conftest.py`` actually bites (otherwise every other "offline" test is
  an unverified assertion about our own discipline);
* the real provider's parsing, repair and error paths are covered by a transport double,
  so a bug there is caught in CI rather than the first time someone exports a key.
"""

from __future__ import annotations

import socket

import httpx
import pytest

from memory_core.errors import ProposalInvalid, SummarizerUnavailable
from memory_core.models import (
    ChallengeMeta,
    GuessingState,
    MemorySnapshot,
    SourceRecord,
    TemplateDefinition,
)
from memory_core.providers.ais3 import AIS3Summarizer
from memory_core.summarizer import extract_json, parse_proposal


def test_the_network_guard_actually_blocks_connections() -> None:
    with pytest.raises(Exception, match="must not make network calls"):
        socket.create_connection(("example.com", 80), timeout=1)


def _client(responses: list[str], recorder: list[dict[str, object]] | None = None) -> httpx.Client:
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        if recorder is not None:
            recorder.append({"url": str(request.url)})
        return httpx.Response(200, json={"choices": [{"message": {"content": queue.pop(0)}}]})

    return httpx.Client(transport=httpx.MockTransport(handler))


def _snapshot() -> MemorySnapshot:
    return MemorySnapshot(
        challenge=ChallengeMeta(id="demo", title="Demo"),
        fact={"general": []},
        assumption={"general": []},
        guessing=GuessingState(catalog="generic@1"),
    )


def _template() -> TemplateDefinition:
    return TemplateDefinition(
        id="generic", version=1, fact_sections=["general"], assumption_sections=["general"]
    )


def _record() -> SourceRecord:
    return SourceRecord(
        id="I-aaaa1111",
        type="input",
        at="2026-07-25T00:00:00Z",
        actor="ctshen",
        input_type="note",
        content={"content": "port 80 is open"},
    )


def test_extract_json_tolerates_fenced_output() -> None:
    assert extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert extract_json('Sure! Here you go: {"a": 1} hope that helps') == '{"a": 1}'
    assert extract_json('{"a": 1}') == '{"a": 1}'


def test_parse_proposal_rejects_non_json() -> None:
    with pytest.raises(ProposalInvalid):
        parse_proposal("I'm afraid I can't do that.")


def test_parse_proposal_rejects_unknown_fields() -> None:
    """A model trying to edit the guessing catalog fails validation outright."""

    with pytest.raises(ProposalInvalid):
        parse_proposal('{"guessing": [{"id": "WEB-XXE", "title": "XXE"}]}')


def test_provider_parses_a_well_formed_response() -> None:
    summarizer = AIS3Summarizer(
        base_url="https://llm-api.zoolab.org/v1",
        api_key="test",
        model="ais3/nemotron-3-ultra-550b",
        client=_client(
            [
                '{"fact_add": [{"section": "general", "text": "TCP/80 is open.", '
                '"evidence": ["I-aaaa1111"]}]}'
            ]
        ),
    )
    proposal = summarizer.summarize(
        state=_snapshot(), source_record=_record(), template=_template()
    )
    assert [i.text for i in proposal.fact_add] == ["TCP/80 is open."]


def test_provider_repairs_one_bad_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bounded repair round-trip, not an unbounded retry loop."""

    summarizer = AIS3Summarizer(
        base_url="https://llm-api.zoolab.org/v1",
        api_key="test",
        model="m",
        client=_client(["not json at all", '{"note": "recovered"}']),
    )
    proposal = summarizer.summarize(
        state=_snapshot(), source_record=_record(), template=_template()
    )
    assert proposal.note == "recovered"


def test_provider_gives_up_after_the_repair_budget() -> None:
    summarizer = AIS3Summarizer(
        base_url="https://llm-api.zoolab.org/v1",
        api_key="test",
        model="m",
        max_repairs=1,
        client=_client(["nope", "still nope"]),
    )
    with pytest.raises(ProposalInvalid):
        summarizer.summarize(state=_snapshot(), source_record=_record(), template=_template())


def test_provider_reports_transport_failure_as_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream down")

    summarizer = AIS3Summarizer(
        base_url="https://llm-api.zoolab.org/v1",
        api_key="test",
        model="m",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(SummarizerUnavailable) as excinfo:
        summarizer.summarize(state=_snapshot(), source_record=_record(), template=_template())
    assert excinfo.value.status == 502


def test_provider_targets_the_documented_endpoint() -> None:
    calls: list[dict[str, object]] = []
    summarizer = AIS3Summarizer(
        base_url="https://llm-api.zoolab.org/v1/",
        api_key="test",
        model="m",
        client=_client(["{}"], recorder=calls),
    )
    summarizer.summarize(state=_snapshot(), source_record=_record(), template=_template())
    assert calls[0]["url"] == "https://llm-api.zoolab.org/v1/chat/completions"


def test_provider_falls_back_to_generic_on_unparseable_classification() -> None:
    """Classification is advisory: a bad answer must not block challenge creation."""

    summarizer = AIS3Summarizer(
        base_url="https://llm-api.zoolab.org/v1",
        api_key="test",
        model="m",
        client=_client(["I think it's a web challenge, probably"]),
    )
    proposal = summarizer.classify(initial_input="a login page", templates=[_template()])
    assert proposal.template == "generic"
