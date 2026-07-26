from __future__ import annotations

import json
import sys

import pytest
from conftest import ASGITestClient

from human_client import cli
from human_client.client import MemoryCoreClient
from human_client.errors import OK, REVISION_CONFLICT
from memory_core.service import MemoryService


@pytest.fixture
def patched_client(monkeypatch: pytest.MonkeyPatch, service: MemoryService) -> None:
    def build(api_url: str, *, timeout: float = 30.0) -> MemoryCoreClient:
        return MemoryCoreClient(api_url, timeout=timeout, http_client=ASGITestClient(service))

    monkeypatch.setattr(cli, "MemoryCoreClient", build)


def test_no_args_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == OK
    assert "ctfmem" in capsys.readouterr().out


def test_note_json_output_is_machine_safe(
    patched_client: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["challenge", "create", "demo-web", "--title", "Demo Web"]) == OK
    capsys.readouterr()

    code = cli.main(
        [
            "--json",
            "--challenge",
            "demo-web",
            "note",
            "I ran nmap and port 80 is open.",
        ]
    )
    captured = capsys.readouterr()

    assert code == OK
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["revision_before"] == 0
    assert payload["revision_after"] == 1
    assert payload["diff"]["fact_added"] == ["F001"]


def test_query_references_are_displayed(
    patched_client: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli.main(["challenge", "create", "demo-web", "--title", "Demo Web"])
    cli.main(["--challenge", "demo-web", "note", "port 80 is open"])
    capsys.readouterr()

    assert cli.main(["--challenge", "demo-web", "query", "which ports are open?"]) == OK
    out = capsys.readouterr().out

    assert "Answer:" in out
    assert "F001" in out


def test_stale_mutation_returns_stable_conflict_exit_code(
    patched_client: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli.main(["challenge", "create", "demo-web", "--title", "Demo Web"])
    cli.main(["--challenge", "demo-web", "note", "port 80 is open"])
    capsys.readouterr()

    code = cli.main(
        [
            "--challenge",
            "demo-web",
            "mutate",
            "add",
            "fact",
            "--text",
            "manual",
            "--reason",
            "stale",
            "--revision",
            "0",
        ]
    )
    captured = capsys.readouterr()

    assert code == REVISION_CONFLICT
    assert "Mutation rejected" in captured.err


def test_capture_ingests_one_command_result(
    patched_client: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli.main(["challenge", "create", "demo-web", "--title", "Demo Web"])
    capsys.readouterr()

    code = cli.main(
        [
            "--json",
            "--challenge",
            "demo-web",
            "capture",
            "--",
            sys.executable,
            "-c",
            "print('80/tcp open http')",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert code == 0
    assert payload["child_exit_code"] == 0
    assert payload["ingest"]["source_id"].startswith("I-")
