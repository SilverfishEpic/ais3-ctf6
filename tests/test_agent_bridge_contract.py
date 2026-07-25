from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from agent_bridge.adapters.fake import FakeAgentAdapter
from agent_bridge.errors import MemoryCoreError
from agent_bridge.loop import AgentLoop, LoopLimits, StopSignal
from agent_bridge.memory.fake import EMPTY_DIFF, FakeMemoryClient
from agent_bridge.memory.http import HttpMemoryClient
from agent_bridge.normalize import AgentRunEvent, NormalizedRun
from agent_bridge.policy import SessionMode
from agent_bridge.protocols import RunResult
from agent_bridge.sandbox import ToolPolicy
from memory_core.models import IngestEnvelope, SourceRecord
from memory_core.summarizer import is_direct_evidence

EXPECTED_FIXTURES = {
    "talk_only",
    "single_tool",
    "multi_tool",
    "tool_failure",
    "sandbox_denied",
    "interrupted",
}


def test_fake_adapter_fixtures_are_committed_and_replayable() -> None:
    assert set(FakeAgentAdapter.available()) == EXPECTED_FIXTURES
    for scenario in sorted(EXPECTED_FIXTURES):
        envelope = FakeAgentAdapter(scenario).run_sync()
        parsed = IngestEnvelope.model_validate(envelope)
        assert parsed.source.type == "agent"
        assert parsed.input.type == "run_log"
        assert parsed.input.events


def test_wire_envelope_preserves_execution_outcomes() -> None:
    outcomes: dict[str, str] = {}
    for scenario in ("single_tool", "tool_failure", "sandbox_denied", "interrupted"):
        events = FakeAgentAdapter(scenario).run_sync()["input"]["events"]
        tool_results = [e for e in events if e["kind"] == "tool_result"]
        assert tool_results
        outcomes[scenario] = tool_results[-1]["outcome"]
        assert "tool_use_id" not in tool_results[-1]
        assert "input" not in tool_results[-1]

    assert outcomes == {
        "single_tool": "succeeded",
        "tool_failure": "failed",
        "sandbox_denied": "denied",
        "interrupted": "interrupted",
    }


def test_agent_outcome_controls_direct_evidence_status() -> None:
    def record(outcome: str) -> SourceRecord:
        return SourceRecord.model_validate(
            {
                "id": "I-agent0001",
                "type": "input",
                "at": "2026-07-26T00:00:00Z",
                "actor": "agent",
                "input_type": "run_log",
                "source": {"type": "agent", "provider": "anthropic"},
                "content": {
                    "events": [
                        {
                            "seq": 1,
                            "role": "tool",
                            "kind": "tool_result",
                            "content": "fixture output",
                            "outcome": outcome,
                        }
                    ]
                },
            }
        )

    assert is_direct_evidence(record("succeeded")) is True
    assert is_direct_evidence(record("failed")) is True
    assert is_direct_evidence(record("denied")) is False
    assert is_direct_evidence(record("interrupted")) is False
    assert is_direct_evidence(record("unknown")) is False


def test_http_memory_client_preserves_core_error_model() -> None:
    async def run() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/challenges/demo/memory"
            return httpx.Response(
                409,
                json={
                    "error": {
                        "code": "revision_conflict",
                        "message": "stale revision",
                        "details": {"current_revision": 4},
                    }
                },
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://core.test"
        ) as raw:
            client = HttpMemoryClient("http://core.test", client=raw)
            with pytest.raises(MemoryCoreError) as caught:
                await client.get_memory("demo")

        assert caught.value.status == 409
        assert caught.value.code == "revision_conflict"
        assert caught.value.current_revision == 4

    asyncio.run(run())


def test_session_mode_must_match_adapter_session_kind() -> None:
    adapter = FakeAgentAdapter("talk_only")
    adapter.session_kind = "same"  # type: ignore[attr-defined]
    memory = FakeMemoryClient()
    with pytest.raises(ValueError):
        AgentLoop(
            adapter=adapter,
            memory=memory,
            challenge_id="demo",
            session=SessionMode(kind="new"),
        )


def test_yolo_refreshes_full_board_after_external_human_revision() -> None:
    async def run() -> None:
        adapter = FakeAgentAdapter("talk_only")
        memory = FakeMemoryClient()
        await memory.create_challenge("demo")
        memory.queue_diffs("demo", {"assumption_added": ["A999"]}, EMPTY_DIFF)
        loop = AgentLoop(
            adapter=adapter,
            memory=memory,
            challenge_id="demo",
            limits=LoopLimits(max_turns=2, idle_turns_before_stop=10),
        )

        first = await loop.run_once("start")
        assert first.board_injected and "# 共享白板" in first.board_injected

        memory.apply_external_fact(
            "demo",
            item_id="F-human",
            text="Human confirmed the CSRF token is missing.",
            section="authentication",
        )
        second = await loop._turn_once("continue", first=False)

        assert second.board_injected
        assert "# 共享白板" in second.board_injected
        assert "Human confirmed the CSRF token is missing." in second.board_injected

    asyncio.run(run())


def test_yolo_stop_requests_adapter_cancel_and_ingests_partial_result() -> None:
    class CancelAwareAdapter:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.cancelled = False
            self.calls: list[dict[str, Any]] = []

        async def start(self, prompt: str, *, board: str | None = None) -> RunResult:
            self.calls.append({"prompt": prompt, "board": board})
            self.started.set()
            while not self.cancelled:
                await asyncio.sleep(0.01)
            run = NormalizedRun(
                events=[
                    AgentRunEvent(
                        seq=1,
                        role="tool",
                        kind="tool_result",
                        content="partial output before interruption",
                        outcome="interrupted",
                    )
                ],
                run_meta={"interrupted": True},
            )
            return RunResult(
                envelope={
                    "source": {"type": "agent", "provider": "fake", "run_id": "cancelled"},
                    "input": {
                        "type": "run_log",
                        "events": [
                            {
                                "seq": 1,
                                "role": "tool",
                                "kind": "tool_result",
                                "content": "partial output before interruption",
                                "outcome": "interrupted",
                            }
                        ],
                    },
                },
                run=run,
            )

        async def continue_run(self, prompt: str, *, board: str | None = None) -> RunResult:
            raise AssertionError("stop test should not continue")

        async def cancel(self) -> None:
            self.cancelled = True

    async def run() -> None:
        adapter = CancelAwareAdapter()
        memory = FakeMemoryClient()
        await memory.create_challenge("demo")
        stop = StopSignal()
        loop = AgentLoop(
            adapter=adapter,
            memory=memory,
            challenge_id="demo",
            limits=LoopLimits(max_turns=2, idle_turns_before_stop=10),
            stop=stop,
        )
        task = asyncio.create_task(loop.run_yolo("start"))
        await adapter.started.wait()
        stop._on_signal()
        outcome = await task

        assert adapter.cancelled is True
        assert len(outcome.turns) == 1
        assert outcome.stopped_because == "Agent 執行被中斷"
        assert memory.ingested[0]["input"]["events"][0]["outcome"] == "interrupted"

    asyncio.run(run())


def test_sandbox_denies_unsafe_shell_invocation() -> None:
    denial = ToolPolicy().check("Bash", {"command": "rm -rf /tmp/not-real"})
    assert denial is not None
