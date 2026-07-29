"""Native shell integration for CTF Memory.

This module deliberately does not allocate a PTY or proxy terminal input/output.  ``shell-init``
starts an isolated child zsh with temporary CTF shortcuts; it never changes the caller's shell
profile, completion, plugins, history, or terminal configuration.
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .client import MemoryCoreClient
from .config import ClientConfig, require_challenge

_SHELL_BEGIN = "# >>> ctfmem shell-init >>>"
_SHELL_END = "# <<< ctfmem shell-init <<<"
_SHORTCUTS = ("ctf", "cap", "note", "mem", "ask", "add", "edit", "guess", "try")


@dataclass
class ShellSession:
    """Small local preferences for the native slash-command integration."""

    challenge: str
    diff_enabled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"version": 1, **asdict(self)}

    @classmethod
    def from_dict(cls, raw: dict[str, Any], *, challenge: str) -> ShellSession:
        if raw.get("challenge") != challenge:
            raise ValueError("shell state belongs to a different challenge")
        return cls(
            challenge=challenge,
            diff_enabled=bool(raw.get("diff_enabled", False)),
        )


class ShellSessionStore:
    def __init__(self, challenge: str, *, root: Path | str = ".ctfmem") -> None:
        self.challenge = challenge
        self.root = Path(root)

    @property
    def path(self) -> Path:
        return self.root / self.challenge / "shell-session.json"

    def load(self) -> ShellSession:
        if not self.path.exists():
            return ShellSession(challenge=self.challenge)
        try:
            import json

            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("shell state must be an object")
            return ShellSession.from_dict(raw, challenge=self.challenge)
        except (OSError, ValueError, json.JSONDecodeError):
            return ShellSession(challenge=self.challenge)

    def save(self, session: ShellSession) -> None:
        if session.challenge != self.challenge:
            raise ValueError("cannot save shell state for another challenge")
        import json

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(session.to_dict(), ensure_ascii=False) + "\n", encoding="utf-8"
        )


def add_shell_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    shell = subparsers.add_parser(
        "shell-init", help="enter a temporary zsh with collision-safe CTF shortcuts"
    )
    shell.add_argument("--shell", choices=("zsh",), default="zsh")
    mode = shell.add_mutually_exclusive_group()
    mode.add_argument("--print", action="store_true", dest="print_only")
    mode.add_argument(
        "--remove",
        action="store_true",
        help="remove a legacy ctfmem marker from ~/.zshrc; no new marker is installed",
    )
    shell.set_defaults(handler=cmd_shell_init)

    command = subparsers.add_parser("shell-command", help=argparse.SUPPRESS)
    command.add_argument("slash")
    command.add_argument("argument", nargs=argparse.REMAINDER)
    command.set_defaults(handler=cmd_shell_command)

    # `/note` and `/memory` are full native CLI aliases registered in cli.py. These two
    # remain session-local shell controls used by sourced shell hooks.
    for slash in ("/status", "/diff"):
        direct = subparsers.add_parser(slash, help=argparse.SUPPRESS)
        direct.add_argument("argument", nargs=argparse.REMAINDER)
        direct.set_defaults(handler=cmd_shell_command, slash=slash)


def cmd_shell_init(
    args: argparse.Namespace, _config: ClientConfig, _client: MemoryCoreClient
) -> int:
    shell = args.shell
    snippet = render_shell_init(shell)
    if args.print_only:
        print(snippet, end="")
        return 0

    if args.remove:
        rc_file = _shell_rc_file(shell)
        if _remove_shell_init(rc_file):
            print(f"ctfmem: removed legacy ctfmem integration from {rc_file}")
        else:
            print(f"ctfmem: no legacy ctfmem integration is installed in {rc_file}")
        return 0

    if not _has_interactive_tty():
        print("ctfmem: no interactive TTY is available to launch a temporary zsh.")
        return 0
    return _launch_shell(shell, snippet)


def _shell_rc_file(shell: str) -> Path:
    home = Path.home()
    if shell == "bash":
        return home / ".bashrc"
    if shell == "zsh":
        return home / ".zshrc"
    if shell == "fish":
        return home / ".config" / "fish" / "config.fish"
    raise ValueError("shell must be bash, zsh, or fish")


def _remove_shell_init(rc_file: Path) -> bool:
    try:
        existing = rc_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ValueError(f"cannot read shell startup file {rc_file}: {exc}") from exc
    if _SHELL_BEGIN not in existing:
        return False
    _write_shell_rc(rc_file, _without_shell_init(existing))
    return True


def _without_shell_init(content: str) -> str:
    begin = content.find(_SHELL_BEGIN)
    if begin == -1:
        return content
    end = content.find(_SHELL_END, begin)
    if end == -1:
        raise ValueError(
            "ctfmem shell-init marker is incomplete; refusing to edit the startup file"
        )
    end += len(_SHELL_END)
    if end < len(content) and content[end] == "\n":
        end += 1
    return content[:begin] + content[end:]


def _write_shell_rc(rc_file: Path, content: str) -> None:
    try:
        rc_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = rc_file.with_suffix(f"{rc_file.suffix}.ctfmem-tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(rc_file)
    except OSError as exc:
        raise ValueError(f"cannot write shell startup file {rc_file}: {exc}") from exc


def _launch_shell(shell: str, snippet: str) -> int:
    executable = shutil.which(shell)
    if executable is None:
        raise ValueError(f"cannot launch {shell}: executable was not found on PATH")
    print(f"ctfmem: entering a temporary {shell} shell; run `exit` to return.")
    with tempfile.TemporaryDirectory(prefix="ctfmem-zsh-") as temporary:
        temporary_root = Path(temporary)
        _write_temporary_zshrc(temporary_root / ".zshrc", snippet)
        environment = os.environ | {"ZDOTDIR": temporary}
        return subprocess.run([executable, "-i"], check=False, env=environment).returncode


def _write_temporary_zshrc(path: Path, snippet: str) -> None:
    original = _shell_rc_file("zsh")
    quoted = shlex.quote(str(original))
    path.write_text(
        f"if [[ -r {quoted} ]]; then\n"
        f"    source {quoted}\n"
        "fi\n"
        f"{snippet}",
        encoding="utf-8",
    )


def _has_interactive_tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def cmd_shell_command(
    args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient
) -> int:
    challenge = require_challenge(config)
    store = ShellSessionStore(challenge)
    session = store.load()
    slash = args.slash
    argument = " ".join(args.argument).strip()
    if slash == "/status":
        health = client.healthz()
        status = health.get("status", "connected")
        print(
            f"ctfmem: diff {'on' if session.diff_enabled else 'off'}; Core {status}"
        )
    elif slash == "/diff":
        if argument not in {"on", "off"}:
            raise ValueError(f"usage: {slash} on|off")
        enabled = argument == "on"
        session.diff_enabled = enabled
        store.save(session)
        print(f"ctfmem: {slash[1:]} {argument}")
    elif slash == "/note":
        if not argument:
            raise ValueError("usage: /note <text>")
        envelope = client.ingest_note(challenge, argument, actor=config.actor)
        _render_shell_diff(envelope, enabled=session.diff_enabled)
    elif slash == "/memory":
        board = client.read_memory(challenge)
        print(_memory_status(board))
    else:
        raise ValueError(f"unknown slash command: {slash}")
    return 0


def _render_shell_diff(envelope: dict[str, Any], *, enabled: bool) -> None:
    if not enabled:
        return
    for line in _ansi_diff_lines(envelope):
        print(line)


def _ansi_diff_lines(envelope: dict[str, Any]) -> list[str]:
    diff = envelope.get("diff")
    if not isinstance(diff, dict):
        return []
    lines: list[str] = []
    for key in ("fact_added", "assumption_added", "promoted"):
        lines.extend(f"\x1b[32m+ {key}: {value}\x1b[0m" for value in _items(diff, key))
    for key in ("fact_removed", "assumption_removed", "demoted"):
        lines.extend(f"\x1b[31m- {key}: {value}\x1b[0m" for value in _items(diff, key))
    for key in ("fact_updated", "assumption_updated"):
        lines.extend(f"\x1b[36m~ {key}: {value}\x1b[0m" for value in _items(diff, key))
    return lines


def _items(diff: dict[str, Any], key: str) -> list[str]:
    values = diff.get(key)
    return [str(value) for value in values] if isinstance(values, list) else []


def _memory_status(board: dict[str, Any]) -> str:
    facts = sum(len(value) for value in board.get("fact", {}).values() if isinstance(value, list))
    assumptions = sum(
        len(value) for value in board.get("assumption", {}).values() if isinstance(value, list)
    )
    return (
        f"ctfmem: revision {board.get('revision', '?')}; facts={facts}; "
        f"assumptions={assumptions}"
    )


def render_shell_init(shell: str) -> str:
    if shell == "bash":
        return _bash_init()
    if shell == "zsh":
        return _zsh_init()
    if shell == "fish":
        return _fish_init()
    raise ValueError("shell must be bash, zsh, or fish")


def _bash_init() -> str:
    return _shell_block("Bash", _bash_shortcuts())


def _zsh_init() -> str:
    return _shell_block("Zsh", _zsh_shortcuts())


def _fish_init() -> str:
    return _shell_block("Fish", _fish_shortcuts())


def _shell_block(shell: str, shortcuts: str) -> str:
    return (
        f"{_SHELL_BEGIN}\n"
        f"# ctfmem native {shell} integration. "
        "It never observes other commands or terminal input.\n"
        f"{shortcuts}"
        f"{_SHELL_END}\n"
    )


def _bash_shortcuts() -> str:
    return _posix_shortcuts(
        "printf '%s\\n' 'ctfmem: {name} was not defined; the name is in use.' >&2"
    )


def _zsh_shortcuts() -> str:
    return _posix_shortcuts("print -u2 'ctfmem: {name} was not defined; the name is in use.'")


def _posix_shortcuts(warning: str) -> str:
    lines: list[str] = []
    for name, body in _shortcut_bodies('"$@"'):
        lines.extend(
            [
                f"if ! command -v {name} >/dev/null 2>&1; then",
                f"    {name}() {{ {body}; }}",
                "else",
                f"    {warning.format(name=name)}",
                "fi",
            ]
        )
    return "\n".join(lines) + "\n"


def _fish_shortcuts() -> str:
    lines: list[str] = []
    for name, body in _shortcut_bodies("$argv"):
        lines.extend(
            [
                f"if not type -q {name}",
                f"    function {name}",
                f"        {body}",
                "    end",
                "else",
                f"    echo 'ctfmem: {name} was not defined; the name is in use.' >&2",
                "end",
            ]
        )
    return "\n".join(lines) + "\n"


def _shortcut_bodies(arguments: str) -> tuple[tuple[str, str], ...]:
    return (
        ("ctf", f"command ctfmem {arguments}"),
        ("cap", f"command ctfmem capture -- {arguments}"),
        ("note", f"command ctfmem /note {arguments}"),
        ("mem", f"command ctfmem /memory {arguments}"),
        ("ask", f"command ctfmem /ask {arguments}"),
        ("add", f"command ctfmem /add {arguments}"),
        ("edit", f"command ctfmem /edit {arguments}"),
        ("guess", f"command ctfmem /guess {arguments}"),
        ("try", f"command ctfmem /try {arguments}"),
    )
