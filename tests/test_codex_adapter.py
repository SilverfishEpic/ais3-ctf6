from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from agent_bridge.adapters.codex import SECRET_ENV_NAMES, CodexAdapter, CodexMemoryMCP
from agent_bridge.adapters.registry import build_agent_adapter
from agent_bridge.memory.fake import FakeMemoryClient
from agent_bridge.policy import FULL
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
            executable=sys.executable,
            model="gpt-fixture",
        )
        adapter.ephemeral = False

        def argv(prompt: str, *, resume: bool = False) -> list[str]:
            assert resume is False
            return [sys.executable, str(script), prompt]

        adapter._argv = argv  # type: ignore[method-assign]
        result = await adapter.start("inspect", board="FACT\n  F001 test")

        assert result.envelope["source"]["provider"] == "openai-codex"
        assert result.envelope["source"]["model"] == "gpt-fixture"
        assert result.envelope["source"]["session_id"] == "thread-fixture"
        assert result.envelope["input"]["events"][-1]["outcome"] == "succeeded"
        IngestEnvelope.model_validate(result.envelope)

    asyncio.run(run())


def test_codex_adapter_same_session_resumes_reported_thread(tmp_path: Path) -> None:
    adapter = CodexAdapter(
        workspace=tmp_path,
        executable="codex",
        model="gpt-fixture",
        session_kind="same",
    )

    first = adapter._argv("first turn")
    assert first[:10] == [
        "codex",
        "--ask-for-approval",
        "untrusted",
        "exec",
        "--json",
        "--ignore-user-config",
        "--ignore-rules",
        "--sandbox",
        "read-only",
        "--cd",
    ]
    assert "--ephemeral" not in first

    adapter._session_id = "thread-abc"
    resumed = adapter._argv("second turn", resume=True)
    assert resumed == [
        "codex",
        "--ask-for-approval",
        "untrusted",
        "exec",
        "resume",
        "--json",
        "--ignore-user-config",
        "--ignore-rules",
        "--model",
        "gpt-fixture",
        "thread-abc",
        "second turn",
    ]


def test_codex_adapter_continue_run_keeps_same_session_id(tmp_path: Path) -> None:
    script = tmp_path / "fake_codex_resume.py"
    script.write_text(
        "\n".join(
            [
                "import json, sys",
                "mode = sys.argv[1]",
                "if mode == 'start':",
                "    print(json.dumps({'type': 'thread.started', 'thread_id': 'thread-same'}))",
                "print(json.dumps({'type': 'item.completed', 'item': {"
                "'id': mode, 'type': 'agent_message', 'text': mode}}))",
            ]
        ),
        encoding="utf-8",
    )
    calls: list[str] = []

    async def run() -> None:
        adapter = CodexAdapter(
            workspace=tmp_path,
            executable=sys.executable,
            session_kind="same",
        )
        adapter._argv = lambda prompt, *, resume=False: [  # type: ignore[method-assign]
            sys.executable,
            str(script),
            "resume" if resume else "start",
            prompt,
        ]

        first = await adapter.start("first")
        calls.append(first.run.events[0].content)
        second = await adapter.continue_run("second")
        calls.append(second.run.events[0].content)

        assert first.envelope["source"]["session_id"] == "thread-same"
        assert second.envelope["source"]["session_id"] == "thread-same"
        assert calls == ["start", "resume"]

    asyncio.run(run())


def test_codex_adapter_new_session_uses_ephemeral_exec(tmp_path: Path) -> None:
    adapter = CodexAdapter(workspace=tmp_path, executable="codex", session_kind="new")

    argv = adapter._argv("single turn")

    assert argv[:10] == [
        "codex",
        "--ask-for-approval",
        "untrusted",
        "exec",
        "--json",
        "--ignore-user-config",
        "--ignore-rules",
        "--sandbox",
        "read-only",
        "--cd",
    ]
    assert "--ephemeral" in argv


def test_registry_allows_codex_same_session_when_memory_skill_is_disabled(tmp_path: Path) -> None:
    adapter = build_agent_adapter(
        provider="codex",
        fake_agent=None,
        workspace=tmp_path,
        model=None,
        max_turns=3,
        max_cost_usd=1.0,
        session="same",
        memory=FakeMemoryClient(),  # type: ignore[arg-type]
        challenge="demo",
        policy=FULL,
        no_memory_skill=True,
    )

    assert isinstance(adapter, CodexAdapter)
    assert adapter.session_kind == "same"


def test_registry_configures_codex_memory_mcp_when_enabled(tmp_path: Path) -> None:
    memory = FakeMemoryClient()
    memory.base_url = "http://core.test"  # type: ignore[attr-defined]

    adapter = build_agent_adapter(
        provider="codex",
        fake_agent=None,
        workspace=tmp_path,
        model=None,
        max_turns=3,
        max_cost_usd=1.0,
        session="same",
        memory=memory,  # type: ignore[arg-type]
        challenge="demo",
        policy=FULL,
        no_memory_skill=False,
    )

    assert isinstance(adapter, CodexAdapter)
    assert adapter.memory_mcp == CodexMemoryMCP(
        api_url="http://core.test",
        challenge="demo",
        policy="full",
        timeout=30.0,
    )


def test_codex_adapter_adds_scoped_mcp_config_args(tmp_path: Path) -> None:
    adapter = CodexAdapter(
        workspace=tmp_path,
        executable="codex",
        memory_mcp=CodexMemoryMCP(
            api_url="http://core.test",
            challenge="demo",
            policy="full",
            timeout=30.0,
            guess_seed=42,
        ),
    )

    argv = adapter._argv("inspect")
    joined = "\n".join(argv)

    assert "mcp_servers.memory.command=" in joined
    assert "agent_bridge.memory_mcp" in joined
    assert "http://core.test" in joined
    assert "demo" in joined
    assert "--guess-seed" in joined


def test_codex_adapter_strips_memory_core_provider_secrets_from_subprocess_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = CodexAdapter(workspace=tmp_path, executable="codex")
    for name in SECRET_ENV_NAMES:
        monkeypatch.setenv(name, f"secret-{name}")

    env = adapter._subprocess_env()

    for name in SECRET_ENV_NAMES:
        assert name not in env


def test_codex_adapter_falls_back_to_plain_text_as_agent_message(tmp_path: Path) -> None:
    script = tmp_path / "fake_codex_text.py"
    script.write_text("print('plain final response')\n", encoding="utf-8")

    async def run() -> None:
        adapter = CodexAdapter(workspace=tmp_path, executable=sys.executable)
        adapter.ephemeral = False

        def argv(prompt: str, *, resume: bool = False) -> list[str]:
            assert prompt == "inspect"
            assert resume is False
            return [sys.executable, str(script)]

        adapter._argv = argv  # type: ignore[method-assign]
        result = await adapter.start("inspect")

        assert result.run.events[0].kind == "message"
        assert result.run.events[0].content == "plain final response"
        json.dumps(result.envelope, ensure_ascii=False)

    asyncio.run(run())
