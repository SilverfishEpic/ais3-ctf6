from __future__ import annotations

from typing import Any, Literal, TypedDict


class IngestEnvelope(TypedDict):
    source: dict[str, Any]
    input: dict[str, Any]


class CommandResult(TypedDict):
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str


Layer = Literal["fact", "assumption", "guessing"]


def human_note_envelope(content: str, actor: str) -> IngestEnvelope:
    return {
        "source": {"type": "human", "actor": actor},
        "input": {"type": "note", "content": content},
    }


def command_result_envelope(result: CommandResult, actor: str) -> IngestEnvelope:
    return {
        "source": {"type": "human", "actor": actor},
        "input": {
            "type": "command_result",
            "command": result["command"],
            "exit_code": result["exit_code"],
            "stdout": result["stdout"],
            "stderr": result["stderr"],
        },
    }
