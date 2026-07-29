from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent_bridge.memory.fake import FakeMemoryClient, demo_snapshot
from human_client import agent_cli, cli
from human_client.errors import OK


class FakeHttpMemoryClient(FakeMemoryClient):
    instances: list[FakeHttpMemoryClient] = []

    def __init__(self, base_url: str, *, timeout: float = 30.0) -> None:
        super().__init__(demo_snapshot("demo-web"))
        self.base_url = base_url
        self.timeout = timeout
        self._challenges["demo-web"] = demo_snapshot("demo-web")
        self.instances.append(self)


@pytest.fixture(autouse=True)
def patch_agent_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeHttpMemoryClient.instances = []
    monkeypatch.setattr(agent_cli, "HttpMemoryClient", FakeHttpMemoryClient)


def test_agent_run_fake_agent_ingests_agent_run_log(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = cli.main(
        [
            "--challenge",
            "demo-web",
            "agent",
            "run",
            "--fake-agent",
            "multi_tool",
            "--runtime-dir",
            str(tmp_path),
            "繼續分析",
        ]
    )
    captured = capsys.readouterr()

    assert code == OK
    assert "Agent run completed." in captured.out
    memory = FakeHttpMemoryClient.instances[0]
    assert len(memory.ingested) == 1
    envelope = memory.ingested[0]
    assert envelope["source"]["type"] == "agent"
    assert envelope["source"]["provider"] == "fake"
    assert envelope["input"]["type"] == "run_log"


def test_agent_run_codex_allows_memory_skill_by_default(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_build_agent_adapter(**kwargs: Any) -> object:
        assert kwargs["provider"] == "codex"
        assert kwargs["no_memory_skill"] is False
        raise ValueError("adapter factory reached")

    monkeypatch.setattr(agent_cli, "build_agent_adapter", fake_build_agent_adapter)

    code = cli.main(
        [
            "--challenge",
            "demo-web",
            "agent",
            "run",
            "--provider",
            "codex",
            "--runtime-dir",
            str(tmp_path),
            "繼續分析",
        ]
    )
    captured = capsys.readouterr()

    assert code != OK
    assert "adapter factory reached" in captured.err


def test_slash_agent_run_accepts_codex_provider(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_build_agent_adapter(**kwargs: Any) -> object:
        assert kwargs["provider"] == "codex"
        raise ValueError("slash adapter factory reached")

    monkeypatch.setattr(agent_cli, "build_agent_adapter", fake_build_agent_adapter)

    code = cli.main(
        [
            "--challenge",
            "demo-web",
            "/agent",
            "run",
            "--provider",
            "codex",
            "--workspace",
            str(tmp_path),
            "繼續分析",
        ]
    )
    captured = capsys.readouterr()

    assert code != OK
    assert "slash adapter factory reached" in captured.err


def test_agent_run_json_output_is_machine_safe(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = cli.main(
        [
            "--json",
            "--challenge",
            "demo-web",
            "agent",
            "run",
            "--fake-agent",
            "single_tool",
            "--runtime-dir",
            str(tmp_path),
            "查一下",
        ]
    )
    captured = capsys.readouterr()

    assert code == OK
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["ok"] is True
    assert payload["mode"] == "normal"
    assert payload["agent"]["tool_calls"] >= 1
    assert payload["memory"]["source_id"].startswith("I-fake")


def test_agent_stop_and_status_use_challenge_local_runtime(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    stop_code = cli.main(
        [
            "--challenge",
            "demo-web",
            "agent",
            "stop",
            "--runtime-dir",
            str(tmp_path),
        ]
    )
    stop_out = capsys.readouterr().out

    assert stop_code == OK
    assert "demo-web" in stop_out
    assert (tmp_path / "demo-web" / "agent.stop").exists()

    status_code = cli.main(
        [
            "--json",
            "--challenge",
            "demo-web",
            "agent",
            "status",
            "--runtime-dir",
            str(tmp_path),
        ]
    )
    status_out = capsys.readouterr().out
    payload: dict[str, Any] = json.loads(status_out)

    assert status_code == OK
    assert payload["ok"] is True
    assert payload["challenge"] == "demo-web"
    assert payload["status"] == "not_started"
    assert payload["stop_file"].endswith("demo-web/agent.stop")


def test_agent_status_reports_completed_run(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        cli.main(
            [
                "--challenge",
                "demo-web",
                "agent",
                "run",
                "--fake-agent",
                "talk_only",
                "--runtime-dir",
                str(tmp_path),
                "說明目前狀態",
            ]
        )
        == OK
    )
    capsys.readouterr()

    assert (
        cli.main(
            [
                "--challenge",
                "demo-web",
                "agent",
                "status",
                "--runtime-dir",
                str(tmp_path),
            ]
        )
        == OK
    )
    out = capsys.readouterr().out

    assert "Agent: completed" in out
    assert "Turns: 1" in out


def test_agent_accepts_human_readable_memory_policy_alias(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = cli.main(
        [
            "--json",
            "--challenge",
            "demo-web",
            "agent",
            "run",
            "--fake-agent",
            "talk_only",
            "--memory-policy",
            "facts-assumptions",
            "--runtime-dir",
            str(tmp_path),
            "說明目前狀態",
        ]
    )
    captured = capsys.readouterr()

    assert code == OK
    assert json.loads(captured.out)["ok"] is True


def test_agent_status_json_keeps_runtime_fields_after_completed_run(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        cli.main(
            [
                "--challenge",
                "demo-web",
                "agent",
                "run",
                "--fake-agent",
                "talk_only",
                "--runtime-dir",
                str(tmp_path),
                "說明目前狀態",
            ]
        )
        == OK
    )
    capsys.readouterr()

    assert (
        cli.main(
            [
                "--json",
                "--challenge",
                "demo-web",
                "agent",
                "status",
                "--runtime-dir",
                str(tmp_path),
            ]
        )
        == OK
    )
    payload: dict[str, Any] = json.loads(capsys.readouterr().out)

    assert payload["status"] == "completed"
    assert payload["runtime_dir"].endswith("demo-web")
    assert payload["stop_file"].endswith("demo-web/agent.stop")
