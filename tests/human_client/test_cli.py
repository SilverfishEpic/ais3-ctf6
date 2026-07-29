from __future__ import annotations

import json
import sys
from io import StringIO

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
            "--wait",
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


def test_add_keeps_web_form_evidence_and_tags(
    patched_client: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["challenge", "create", "demo-web", "--title", "Demo Web"]) == OK
    capsys.readouterr()

    assert (
        cli.main(
            [
                "--challenge",
                "demo-web",
                "/add",
                "fact",
                "--text",
                "port 443 is open",
                "--section",
                "target",
                "--reason",
                "manual verification",
                "--evidence",
                "I-0001",
                "--tag",
                "network",
                "--tag",
                "tls",
            ]
        )
        == OK
    )
    capsys.readouterr()

    assert cli.main(["--json", "--challenge", "demo-web", "show", "--facts"]) == OK
    board = json.loads(capsys.readouterr().out)
    item = next(item for item in board["fact"]["target"] if item["text"] == "port 443 is open")
    assert item["tags"] == ["network", "tls"]
    assert "I-0001" in item["evidence"]

    assert (
        cli.main(
            [
                "--challenge",
                "demo-web",
                "/edit",
                "replace",
                item["id"],
                "--text",
                "port 443 is open",
                "--reason",
                "remove mistaken annotations",
                "--clear-evidence",
                "--clear-tags",
            ]
        )
        == OK
    )
    capsys.readouterr()
    assert cli.main(["--json", "--challenge", "demo-web", "show", "--facts"]) == OK
    board = json.loads(capsys.readouterr().out)
    item = next(item for item in board["fact"]["target"] if item["text"] == "port 443 is open")
    assert item["evidence"] == []
    assert item["tags"] == []


def test_slash_commands_reuse_the_long_form_handlers(
    patched_client: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["/challenge", "create", "slash-demo", "--title", "Slash Demo"]) == OK
    capsys.readouterr()

    assert cli.main(["--challenge", "slash-demo", "/note", "port 80 is open"]) == OK
    assert (
        cli.main(
            [
                "--challenge",
                "slash-demo",
                "/edit",
                "add",
                "fact",
                "--text",
                "manual fact",
                "--reason",
                "verified manually",
            ]
        )
        == OK
    )
    assert cli.main(["--challenge", "slash-demo", "/memory", "--facts"]) == OK
    out = capsys.readouterr().out

    assert "Memory rev 1 → 2" in out
    assert "manual fact" in out


def test_slash_command_accepts_multiline_json_from_stdin(
    patched_client: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli.main(["challenge", "create", "slash-demo", "--title", "Slash Demo"])
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO('{"command": ["probe"], "exit_code": 1, "stdout": "one\\ntwo", "stderr": "bad"}'),
    )

    assert cli.main(["--challenge", "slash-demo", "/command", "--stdin"]) == OK
    assert "Memory rev 0 → 1" in capsys.readouterr().out


def test_slash_stale_mutation_renders_current_board_without_retrying(
    patched_client: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli.main(["challenge", "create", "slash-demo", "--title", "Slash Demo"])
    cli.main(["--challenge", "slash-demo", "note", "port 80 is open"])
    capsys.readouterr()

    code = cli.main(
        [
            "--challenge",
            "slash-demo",
            "/edit",
            "add",
            "fact",
            "--text",
            "stale",
            "--reason",
            "test conflict",
            "--revision",
            "0",
        ]
    )
    captured = capsys.readouterr()

    assert code == REVISION_CONFLICT
    assert "Current whiteboard:" in captured.err
    assert "Revision: 1" in captured.out
