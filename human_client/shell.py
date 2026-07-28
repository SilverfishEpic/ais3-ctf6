"""Native shell integration for CTF Memory.

This module deliberately does not allocate a PTY or proxy terminal input/output.  Operators
source a small, session-only snippet into their existing shell, which adds slash aliases and
post-command hooks while leaving the shell process in control of completion, plugins, and
history.
"""

from __future__ import annotations

import argparse
import os
import shlex
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .client import MemoryCoreClient
from .config import ClientConfig, require_challenge


@dataclass
class ShellSession:
    """Small local preferences; command output is never persisted by the integration."""

    challenge: str
    autonote: bool = True
    diff_enabled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"version": 1, **asdict(self)}

    @classmethod
    def from_dict(cls, raw: dict[str, Any], *, challenge: str) -> ShellSession:
        if raw.get("challenge") != challenge:
            raise ValueError("shell state belongs to a different challenge")
        return cls(
            challenge=challenge,
            autonote=bool(raw.get("autonote", True)),
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
    shell = subparsers.add_parser("shell-init", help="print native Bash/Zsh/Fish integration")
    shell.add_argument("--shell", choices=("bash", "zsh", "fish"))
    shell.set_defaults(handler=cmd_shell_init)

    command = subparsers.add_parser("shell-command", help=argparse.SUPPRESS)
    command.add_argument("slash")
    command.add_argument("argument", nargs=argparse.REMAINDER)
    command.set_defaults(handler=cmd_shell_command)

    autonote = subparsers.add_parser("shell-autonote", help=argparse.SUPPRESS)
    autonote.add_argument("--exit-code", required=True, type=int)
    autonote.add_argument("command", nargs=argparse.REMAINDER)
    autonote.set_defaults(handler=cmd_shell_autonote)

    # `/note` and `/memory` are full native CLI aliases registered in cli.py. These three
    # remain session-local shell controls used by sourced shell hooks.
    for slash in ("/status", "/autonote", "/diff"):
        direct = subparsers.add_parser(slash, help=argparse.SUPPRESS)
        direct.add_argument("argument", nargs=argparse.REMAINDER)
        direct.set_defaults(handler=cmd_shell_command, slash=slash)


def cmd_shell_init(
    args: argparse.Namespace, _config: ClientConfig, _client: MemoryCoreClient
) -> int:
    shell = args.shell or _shell_from_environment()
    print(render_shell_init(shell))
    return 0


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
            f"ctfmem: autonote {'on' if session.autonote else 'off'}; "
            f"diff {'on' if session.diff_enabled else 'off'}; Core {status}"
        )
    elif slash in {"/autonote", "/diff"}:
        if argument not in {"on", "off"}:
            raise ValueError(f"usage: {slash} on|off")
        enabled = argument == "on"
        if slash == "/autonote":
            session.autonote = enabled
        else:
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


def cmd_shell_autonote(
    args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient
) -> int:
    """Ingest command metadata from a native-shell hook without ever wrapping execution."""

    challenge = require_challenge(config)
    session = ShellSessionStore(challenge).load()
    raw = " ".join(args.command).strip()
    try:
        argv = shlex.split(raw)
    except ValueError:
        return 0
    if not session.autonote or not _should_autonote(argv, args.exit_code):
        return 0
    envelope = client.ingest_command_result(
        challenge,
        {"command": argv, "exit_code": args.exit_code, "stdout": "", "stderr": ""},
        actor=config.actor,
    )
    _render_shell_diff(envelope, enabled=session.diff_enabled)
    return 0


def _should_autonote(argv: list[str], exit_code: int) -> bool:
    if not argv or argv[0] in {"cd", "clear", "exit", "history", "pwd", "ctfmem"}:
        return False
    if argv[0].startswith("__ctfmem_"):
        return False
    return exit_code != 0 or argv[0] not in {"ls", "echo", "true"}


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
    prompt_variable = "PROMPT_" + "COMMAND"
    return '''# ctfmem native Bash integration; source in the current shell only.
# Slash commands remain native CLI subcommands: ctfmem /status, ctfmem /note <text>.
__ctfmem_history_cursor=$HISTCMD
__ctfmem_after_command() {
  local status=$?
  if (( HISTCMD <= __ctfmem_history_cursor )); then return; fi
  __ctfmem_history_cursor=$HISTCMD
  local line
  line=$(builtin history 1)
  line=${line#*  }
  command ctfmem shell-autonote --exit-code "$status" -- "$line" 2>/dev/null &
}
''' + (
        prompt_variable
        + '="__ctfmem_after_command${'
        + prompt_variable
        + ':+; $'
        + prompt_variable
        + '}"'
    )


def _zsh_init() -> str:
    return '''# ctfmem native Zsh integration; source in the current shell only.
# Slash commands remain native CLI subcommands: ctfmem /status, ctfmem /note <text>.
function __ctfmem_preexec() { __ctfmem_last_command="$1"; }
function __ctfmem_precmd() {
  local status=$?
  [[ -z "$__ctfmem_last_command" ]] && return
  command ctfmem shell-autonote --exit-code "$status" -- "$__ctfmem_last_command" 2>/dev/null &!
  unset __ctfmem_last_command
}
autoload -Uz add-zsh-hook
add-zsh-hook preexec __ctfmem_preexec
add-zsh-hook precmd __ctfmem_precmd'''


def _fish_init() -> str:
    return '''# ctfmem native Fish integration; source in the current shell only.
# Slash commands remain native CLI subcommands: ctfmem /status, ctfmem /note <text>.
function __ctfmem_postexec --on-event fish_postexec
    set -l command_line (commandline -p)
    if test -n "$command_line"
        command ctfmem shell-autonote --exit-code $status -- "$command_line" 2>/dev/null &
    end
end'''
