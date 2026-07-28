from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from agent_bridge.adapters.codex import CodexAdapter
from memory_core.models import IngestEnvelope


def test_codex_adapter_runs_jsonl_subprocess_and_builds_envelope(tmp_path: Path) -> None:
    script = tmp_path / "fake_codex.py"
    script.write_text(
        "\n".join(
            [
                "import json, sys",
                "print(json.dumps({'type': 'thread.started', 'thread_id': 'thread-fixture'}))",
                "print(json.dumps({'type': 'item.completed', 'item': {"
                "'id': 'msg', 'type': 'agent_message', 'text': 'done'}}))",
                "print(json.dumps({'type': 'item.completed', 'item': {"
                "'id': 'cmd', 'type': 'command_execution', 'command': 'ls', "
                "'exit_code': 0, 'stdout': 'ok'}}))",
                "print('saw prompt=' + sys.argv[-1], file=sys.stderr)",
            ]
        ),
        encoding="utf-8",
    )

    async def run() -> None:
        adapter = CodexAdapter(
            workspace=tmp_path,
            executable=os.environ.get("PYTHON", "python"),
            model="gpt-fixture",
        )
        adapter.ephemeral = False
        adapter._argv = lambda prompt: [  # type: ignore[method-assign]
            os.environ.get("PYTHON", "python"),
            str(script),
            prompt,
        ]
        result = await adapter.start("inspect", board="FACT\n  F001 test")

        assert result.envelope["source"]["provider"] == "openai-codex"
        assert result.envelope["source"]["model"] == "gpt-fixture"
        assert result.envelope["source"]["session_id"] == "thread-fixture"
        assert result.envelope["input"]["events"][-1]["outcome"] == "succeeded"
        IngestEnvelope.model_validate(result.envelope)

    asyncio.run(run())


def test_codex_adapter_falls_back_to_plain_text_as_agent_message(tmp_path: Path) -> None:
    script = tmp_path / "fake_codex_text.py"
    script.write_text("print('plain final response')\n", encoding="utf-8")

    async def run() -> None:
        adapter = CodexAdapter(workspace=tmp_path, executable=os.environ.get("PYTHON", "python"))
        adapter.ephemeral = False
        adapter._argv = lambda prompt: [  # type: ignore[method-assign]
            os.environ.get("PYTHON", "python"),
            str(script),
        ]
        result = await adapter.start("inspect")

        assert result.run.events[0].kind == "message"
        assert result.run.events[0].content == "plain final response"
        json.dumps(result.envelope, ensure_ascii=False)

    asyncio.run(run())
