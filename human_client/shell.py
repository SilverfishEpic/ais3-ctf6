"""Native shell integration for CTF Memory.

This module deliberately does not allocate a PTY or proxy terminal input/output.  ``shell-init``
installs one static, marked shell function so future shells gain ``cap`` without altering the
current shell or its completion, plugins, and history.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .client import MemoryCoreClient
from .config import ClientConfig, require_challenge

_SHELL_BEGIN = "# >>> ctfmem shell-init >>>"
_SHELL_END = "# <<< ctfmem shell-init <<<"
_CAP_DEFINITION = re.compile(
    r"^\s*(?:alias\s+cap(?:=|\s)|function\s+cap\b|cap\s*\(\s*\))", re.MULTILINE
)


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
        "shell-init", help="install collision-safe cap integration for future shells"
    )
    shell.add_argument("--shell", choices=("bash", "zsh", "fish"))
    mode = shell.add_mutually_exclusive_group()
    mode.add_argument("--print", action="store_true", dest="print_only")
    mode.add_argument("--remove", action="store_true")
    shell.add_argument(
        "--rc-file",
        type=Path,
        help="override the shell startup file (useful for a controlled profile)",
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
    shell = args.shell or _shell_from_environment()
    snippet = render_shell_init(shell)
    if args.print_only:
        print(snippet, end="")
        return 0

    rc_file = args.rc_file or _shell_rc_file(shell)
    if args.remove:
        if _remove_shell_init(rc_file):
            print(f"ctfmem: removed cap integration from {rc_file}")
        else:
            print(f"ctfmem: cap integration is not installed in {rc_file}")
        return 0

    _install_shell_init(rc_file, snippet)
    print(f"ctfmem: installed cap integration in {rc_file}")
    print(f"ctfmem: open a new {shell} shell; the current shell is unchanged.")
    return 0


def _shell_rc_file(shell: str) -> Path:
    home = Path.home()
    if shell == "bash":
        return home / ".bashrc"
    if shell == "zsh":
        return home / ".zshrc"
    if shell == "fish":
        return home / ".config" / "fish" / "config.fish"
    raise ValueError("shell must be bash, zsh, or fish")


def _install_shell_init(rc_file: Path, snippet: str) -> None:
    try:
        existing = rc_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        existing = ""
    except OSError as exc:
        raise ValueError(f"cannot read shell startup file {rc_file}: {exc}") from exc

    without_ours = _without_shell_init(existing)
    if _CAP_DEFINITION.search(without_ours):
        raise ValueError(
            f"refusing to install: {rc_file} already defines cap; no existing command was changed"
        )
    command = shutil.which("cap")
    if command is not None:
        raise ValueError(
            f"refusing to install: cap already resolves to {command}; "
            "no existing command was changed"
        )

    separator = "" if not without_ours or without_ours.endswith("\n") else "\n"
    _write_shell_rc(rc_file, f"{without_ours}{separator}{snippet}")


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


def _shell_from_environment() -> str:
    name = Path(os.environ.get("SHELL", "")).name
    return name if name in {"bash", "zsh", "fish"} else "bash"


def render_shell_init(shell: str) -> str:
    if shell == "bash":
        return _bash_init()
    if shell == "zsh":
        return _zsh_init()
    if shell == "fish":
        return _fish_init()
    raise ValueError("shell must be bash, zsh, or fish")


def _bash_init() -> str:
    return f'''{_SHELL_BEGIN}
# ctfmem native Bash integration.  It never observes other commands or terminal input.
if ! command -v cap >/dev/null 2>&1; then
    cap() {{ command ctfmem capture -- "$@"; }}
else
    printf '%s\\n' 'ctfmem: cap was not defined because the name is already in use.' >&2
fi
{_SHELL_END}
'''


def _zsh_init() -> str:
    return f'''{_SHELL_BEGIN}
# ctfmem native Zsh integration.  It never observes other commands or terminal input.
if ! command -v cap >/dev/null 2>&1; then
    cap() {{ command ctfmem capture -- "$@"; }}
else
    print -u2 'ctfmem: cap was not defined because the name is already in use.'
fi
{_SHELL_END}
'''


def _fish_init() -> str:
    return f'''{_SHELL_BEGIN}
# ctfmem native Fish integration.  It never observes other commands or terminal input.
if not type -q cap
    function cap
        command ctfmem capture -- $argv
    end
else
    echo 'ctfmem: cap was not defined because the name is already in use.' >&2
end
{_SHELL_END}
'''
