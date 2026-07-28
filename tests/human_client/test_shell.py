from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from typing import Any

from human_client import cli
from human_client.cli import _normalise_global_options
from human_client.config import ClientConfig
from human_client.shell import (
    ShellSession,
    ShellSessionStore,
    _ansi_diff_lines,
    cmd_shell_command,
    render_shell_init,
)


class FakeShellClient:
    def __init__(self) -> None:
        self.commands: list[dict[str, object]] = []
        self.notes: list[str] = []

    def healthz(self) -> dict[str, Any]:
        return {"status": "ok"}

    def ingest_command_result(
        self, challenge: str, result: dict[str, object], *, actor: str
    ) -> dict[str, Any]:
        assert challenge == "web-01"
        assert actor == "hacker"
        self.commands.append(result)
        return {"revision_after": 2, "diff": {"fact_added": ["F001"]}}

    def ingest_note(self, challenge: str, text: str, *, actor: str) -> dict[str, Any]:
        assert challenge == "web-01"
        assert actor == "hacker"
        self.notes.append(text)
        return {"revision_after": 2, "diff": {"assumption_added": ["A001"]}}

    def read_memory(self, challenge: str) -> dict[str, Any]:
        assert challenge == "web-01"
        return {"revision": 3, "fact": {"web": ["F001"]}, "assumption": {}}


def _config() -> ClientConfig:
    return ClientConfig("http://core.invalid", "web-01", "hacker", 1, False, False, False)


def test_shell_session_persists_only_preferences(tmp_path: Path) -> None:
    store = ShellSessionStore("web-01", root=tmp_path)
    store.save(ShellSession(challenge="web-01", diff_enabled=True))

    restored = store.load()

    assert restored.diff_enabled is True
    assert "stdout" not in store.path.read_text(encoding="utf-8")


def test_bash_init_uses_aliases_and_is_valid_shell_syntax(tmp_path: Path) -> None:
    script = render_shell_init("bash")
    source = tmp_path / "ctfmem.bash"
    source.write_text(script, encoding="utf-8")

    result = subprocess.run(
        ["bash", "-n", str(source)], check=False, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr
    assert "command_not_found_handle" not in script
    assert "shell-autonote" not in script


def test_zsh_and_fish_init_do_not_add_command_hooks() -> None:
    zsh = render_shell_init("zsh")
    fish = render_shell_init("fish")

    assert "add-zsh-hook preexec" not in zsh
    assert "fish_command_not_found" not in fish
    assert "fish_postexec" not in fish


def test_native_cli_accepts_slash_subcommands(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    monkeypatch.chdir(tmp_path)

    code = cli.main(["--challenge", "web-01", "/diff", "on"])

    assert code == 0
    assert "ctfmem: diff on" in capsys.readouterr().out


def test_native_cli_accepts_trailing_global_options_and_compact_commands(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    monkeypatch.chdir(tmp_path)

    code = cli.main(["/diff", "on", "--challenge", "web-01"])

    assert code == 0
    assert "ctfmem: diff on" in capsys.readouterr().out
    assert _normalise_global_options(["test/memory"]) == ["--challenge", "test", "/memory"]
    assert _normalise_global_options(["/memory", "--challenge", "test"]) == [
        "--challenge",
        "test",
        "/memory",
    ]


def test_slash_toggles_and_note_render_ansi_diff(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    monkeypatch.chdir(tmp_path)
    client = FakeShellClient()
    config = _config()

    cmd_shell_command(argparse.Namespace(slash="/diff", argument=["on"]), config, client)  # type: ignore[arg-type]
    cmd_shell_command(
        argparse.Namespace(slash="/note", argument=["check", "the", "cookie"]), config, client
    )  # type: ignore[arg-type]

    output = capsys.readouterr().out
    assert "ctfmem: diff on" in output
    assert "\x1b[32m+ assumption_added: A001\x1b[0m" in output
    assert client.notes == ["check the cookie"]


def test_ansi_diff_uses_green_additions_and_red_removals() -> None:
    lines = _ansi_diff_lines(
        {"diff": {"fact_added": ["F001"], "assumption_removed": ["A001"]}}
    )

    assert lines == [
        "\x1b[32m+ fact_added: F001\x1b[0m",
        "\x1b[31m- assumption_removed: A001\x1b[0m",
    ]
