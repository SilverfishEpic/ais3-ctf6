from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

from human_client import cli
from human_client.config import ClientConfig
from human_client.errors import CONNECTION_ERROR, CoreConnectionError
from human_client.models import CommandResult
from human_client.pending_capture import PendingCaptureStore


def test_pending_capture_store_keeps_raw_output_until_removed(tmp_path: Path) -> None:
    store = PendingCaptureStore(tmp_path / "pending-captures.json")
    pending = store.enqueue(
        challenge="demo",
        actor="operator",
        result=CommandResult(
            command=["probe"],
            exit_code=0,
            stdout="preview �",
            stderr="",
            stdout_base64="/w==",
            stderr_base64="",
        ),
    )

    restored = store.get(pending.id)
    assert restored.result["stdout_base64"] == "/w=="
    assert restored.summary()["command"] == ["probe"]

    store.remove(pending.id)
    assert store.load() == []


def test_capture_queues_result_when_ingest_connection_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class FailingClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def close(self) -> None:
            pass

        def ingest_command_result(self, *_args: object, **_kwargs: object) -> object:
            raise CoreConnectionError("core unavailable")

    monkeypatch.setattr(cli, "MemoryCoreClient", FailingClient)
    config_path = tmp_path / "client.json"

    code = cli.main(
        [
            "--config",
            str(config_path),
            "--challenge",
            "demo",
            "capture",
            "--",
                sys.executable,
                "-c",
                "print('captured output')",
        ]
    )

    assert code == CONNECTION_ERROR
    assert "Capture output retained" in capsys.readouterr().err
    pending = PendingCaptureStore(tmp_path / "pending-captures.json").load()
    assert len(pending) == 1
    assert pending[0].result["stdout_base64"] == "Y2FwdHVyZWQgb3V0cHV0Cg=="


def test_pending_capture_retry_renders_its_actual_ingest_payload(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "client.json"
    store = PendingCaptureStore(tmp_path / "pending-captures.json")
    pending = store.enqueue(
        challenge="demo",
        actor="operator",
        result=CommandResult(command=["probe"], exit_code=0, stdout="", stderr=""),
    )

    class SuccessfulClient:
        def ingest_command_result(self, *_args: object, **_kwargs: object) -> object:
            return {"source_id": "I-0001", "revision_before": 0, "revision_after": 1}

    config = ClientConfig("http://test", None, "operator", 30, True, False, True)
    args = argparse.Namespace(config=str(config_path), id=pending.id)

    assert cli.cmd_pending_capture_retry(args, config, SuccessfulClient()) == 0  # type: ignore[arg-type]
    assert json.loads(capsys.readouterr().out) == {
        "ok": True,
        "pending_capture_id": pending.id,
        "ingest": {"source_id": "I-0001", "revision_before": 0, "revision_after": 1},
    }
    assert store.load() == []
