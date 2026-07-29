from __future__ import annotations

from agent_bridge.normalize_codex import normalize_codex


def test_codex_normalizer_maps_agent_message_and_command_success() -> None:
    run = normalize_codex(
        [
            {"type": "thread.started", "thread_id": "thread-123"},
            {
                "type": "item.completed",
                "item": {"id": "msg-1", "type": "agent_message", "text": "I inspected the repo."},
            },
            {
                "type": "item.started",
                "item": {
                    "id": "cmd-1",
                    "type": "command_execution",
                    "command": "ls",
                    "status": "in_progress",
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "id": "cmd-1",
                    "type": "command_execution",
                    "command": "ls",
                    "exit_code": 0,
                    "stdout": "README.md\n",
                    "status": "completed",
                },
            },
        ]
    )

    assert run.run_meta["session_id"] == "thread-123"
    assert [event.kind for event in run.events] == ["message", "tool_call", "tool_result"]
    assert run.events[1].name == "command_execution"
    assert run.events[1].content == "ls"
    assert run.events[2].outcome == "succeeded"
    assert run.events[2].content == "README.md\n"


def test_codex_normalizer_maps_non_zero_exit_as_failed() -> None:
    run = normalize_codex(
        [
            {
                "type": "item.completed",
                "item": {
                    "id": "cmd-1",
                    "type": "command_execution",
                    "command": "grep needle missing",
                    "exit_code": 2,
                    "stderr": "missing: No such file or directory",
                },
            }
        ]
    )

    assert [event.kind for event in run.events] == ["tool_call", "tool_result"]
    assert run.events[-1].outcome == "failed"
    assert run.events[-1].is_error is True


def test_codex_normalizer_maps_denied_interrupted_and_unknown_conservatively() -> None:
    run = normalize_codex(
        [
            {
                "type": "item.completed",
                "item": {
                    "id": "cmd-denied",
                    "type": "command_execution",
                    "command": "rm -rf /tmp/nope",
                    "status": "denied",
                    "output": "blocked by policy",
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "id": "cmd-interrupted",
                    "type": "command_execution",
                    "command": "sleep 60",
                    "status": "interrupted",
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "id": "cmd-unknown",
                    "type": "command_execution",
                    "command": "mystery",
                    "status": "completed_without_exit",
                },
            },
        ]
    )

    outcomes = [event.outcome for event in run.events if event.kind == "tool_result"]
    assert outcomes == ["denied", "interrupted", "unknown"]
    assert run.run_meta["interrupted"] is True


def test_codex_normalizer_maps_mcp_tool_call() -> None:
    run = normalize_codex(
        [
            {
                "type": "item.started",
                "item": {
                    "id": "mcp-1",
                    "type": "mcp_tool_call",
                    "name": "memory_show",
                    "input": {"challenge": "demo-web"},
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "id": "mcp-1",
                    "type": "mcp_tool_call",
                    "name": "memory_show",
                    "status": "completed",
                    "result": "Revision 3",
                },
            },
        ]
    )

    assert run.events[0].kind == "tool_call"
    assert run.events[0].name == "memory_show"
    assert run.events[1].outcome == "succeeded"
