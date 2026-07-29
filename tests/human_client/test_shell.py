from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from typing import Any

from human_client import cli
from human_client import shell as shell_module
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


def test_bash_init_is_collision_safe_and_valid_shell_syntax(tmp_path: Path) -> None:
    script = render_shell_init("bash")
    source = tmp_path / "ctfmem.bash"
    source.write_text(script, encoding="utf-8")

    result = subprocess.run(
        ["bash", "-n", str(source)], check=False, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr
    assert "command_not_found_handle" not in script
    assert "shell-autonote" not in script
    assert "# >>> ctfmem shell-init >>>" in script
    assert 'cap() { command ctfmem capture -- "$@"; }' in script
    assert "command -v cap" in script
    for shortcut in ("ctf", "note", "mem", "ask", "add", "edit", "guess", "try"):
        assert f"{shortcut}()" in script


def test_zsh_and_fish_init_do_not_add_command_hooks() -> None:
    zsh = render_shell_init("zsh")
    fish = render_shell_init("fish")

    assert "add-zsh-hook preexec" not in zsh
    assert "fish_command_not_found" not in fish
    assert "fish_postexec" not in fish
    assert "function cap" in fish
    assert "function mem" in fish


def test_shell_init_removes_only_a_legacy_marker(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    rc_file = tmp_path / ".zshrc"
    rc_file.write_text(
        "export KEEP_ME=1\n# >>> ctfmem shell-init >>>\nlegacy\n# <<< ctfmem shell-init <<<\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(shell_module, "_shell_rc_file", lambda _shell: rc_file)

    assert cli.main(["shell-init", "--remove"]) == 0
    assert rc_file.read_text(encoding="utf-8") == "export KEEP_ME=1\n"
    assert "removed legacy" in capsys.readouterr().out


def test_shell_init_does_not_write_the_user_startup_file(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    rc_file = tmp_path / ".zshrc"
    original = "alias cap='other-tool'\n"
    rc_file.write_text(original, encoding="utf-8")
    launched: list[str] = []
    monkeypatch.setattr(shell_module, "_shell_rc_file", lambda _shell: rc_file)
    monkeypatch.setattr(
        shell_module,
        "_launch_shell",
        lambda name, snippet: launched.append(f"{name}:{'cap()' in snippet}") or 0,
    )
    monkeypatch.setattr(shell_module, "_has_interactive_tty", lambda: True)

    assert cli.main(["shell-init"]) == 0
    assert rc_file.read_text(encoding="utf-8") == original
    assert launched == ["zsh:True"]
    assert "installed" not in capsys.readouterr().out


def test_shell_init_print_does_not_write_a_startup_file(tmp_path: Path, capsys: Any) -> None:
    rc_file = tmp_path / ".zshrc"

    assert cli.main(["shell-init", "--print"]) == 0

    assert not rc_file.exists()
    assert "# >>> ctfmem shell-init >>>" in capsys.readouterr().out


def test_shell_init_launches_a_temporary_shell_without_installing(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    rc_file = tmp_path / ".zshrc"
    launched: list[tuple[str, str]] = []
    monkeypatch.setattr(shell_module, "_shell_rc_file", lambda _shell: rc_file)
    monkeypatch.setattr(
        shell_module, "_launch_shell", lambda name, snippet: launched.append((name, snippet)) or 0
    )
    monkeypatch.setattr(shell_module, "_has_interactive_tty", lambda: True)

    assert cli.main(["shell-init", "--shell", "zsh"]) == 0

    assert launched[0][0] == "zsh"
    assert "cap()" in launched[0][1]
    assert not rc_file.exists()
    assert "entering" not in capsys.readouterr().out


def test_sourced_shell_block_keeps_an_existing_shortcut(tmp_path: Path) -> None:
    source = tmp_path / "ctfmem.bash"
    source.write_text(render_shell_init("bash"), encoding="utf-8")
    command = f"cap() {{ echo original; }}; source {source}; cap"

    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", command],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout == "original\n"
    assert "cap was not defined" in result.stderr


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
