from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from core.human_cli.client import MemoryCoreClient, MemoryCoreHTTPError


class CommandFilter:
    TRIVIAL_PATTERNS: list[re.Pattern[str]] = [
        re.compile(pattern)
        for pattern in [
            r"^cd\b",
            r"^ls\b",
            r"^dir\b",
            r"^clear\b",
            r"^cls\b",
            r"^exit\b",
            r"^pwd\b",
        ]
    ]

    @classmethod
    def is_trivial(cls, command: str) -> bool:
        cmd = command.strip()
        return (
            not cmd
            or len(cmd) <= 2
            or any(pattern.search(cmd) for pattern in cls.TRIVIAL_PATTERNS)
        )


class LocalStorage:
    @staticmethod
    def append_jsonl(path: Path, item: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(item, ensure_ascii=False) + "\n")

    @staticmethod
    def resolve_path(path_value: str | Path, base_dir: Path) -> Path:
        path = Path(path_value)
        return path if path.is_absolute() else base_dir / path


class PowerShellNoteApp:
    """Human Client shell controller backed by the public Memory Core API."""

    DEFAULT_MEMORY_CORE_URL = "http://127.0.0.1:8000"

    def __init__(self, script_dir: Path) -> None:
        self.script_dir = script_dir
        self.args = self._parse_args()
        self.client = MemoryCoreClient(
            base_url=self.args.memory_core_url,
            timeout=self.args.timeout,
        )

    @staticmethod
    def utc_now() -> str:
        return datetime.now(UTC).isoformat()

    def _parse_args(self) -> argparse.Namespace:
        parser = argparse.ArgumentParser(
            description="Launch an interactive shell with a Memory Core backed `note` command."
        )
        parser.add_argument("--send-note", action="store_true", help=argparse.SUPPRESS)
        parser.add_argument("--send-command-result", action="store_true", help=argparse.SUPPRESS)
        parser.add_argument("--auto-note", action="store_true", help=argparse.SUPPRESS)
        parser.add_argument("--command-text", default="", help=argparse.SUPPRESS)
        parser.add_argument("--exit-code", type=int, default=0, help=argparse.SUPPRESS)
        parser.add_argument("--stdout-file", type=Path, help=argparse.SUPPRESS)
        parser.add_argument("--stderr-file", type=Path, help=argparse.SUPPRESS)
        parser.add_argument("--note-text", default="", help=argparse.SUPPRESS)
        parser.add_argument("--note-stdin", action="store_true", help=argparse.SUPPRESS)
        parser.add_argument("--cwd", default=str(Path.cwd()), help=argparse.SUPPRESS)
        parser.add_argument(
            "--memory-core-url",
            default=os.getenv("MEMORY_CORE_BASE_URL", self.DEFAULT_MEMORY_CORE_URL),
        )
        parser.add_argument(
            "--challenge-id",
            default=os.getenv("MEMORY_CORE_CHALLENGE_ID", "demo"),
        )
        parser.add_argument(
            "--actor",
            default=os.getenv("MEMORY_CORE_ACTOR", os.getenv("USER", "human")),
        )
        parser.add_argument(
            "--rounds-file",
            type=Path,
            default=Path(os.getenv("MEMORY_CORE_ROUNDS_FILE", "rounds.jsonl")),
        )
        parser.add_argument("--shell", default=os.getenv("MEMORY_CORE_SHELL"))
        parser.add_argument(
            "--timeout",
            type=float,
            default=float(os.getenv("MEMORY_CORE_CLIENT_TIMEOUT", "30")),
        )
        parser.add_argument("--json", action="store_true", help="Print raw JSON responses.")
        args = parser.parse_args()

        args.rounds_file = LocalStorage.resolve_path(args.rounds_file, self.script_dir)
        if args.stdout_file:
            args.stdout_file = LocalStorage.resolve_path(args.stdout_file, self.script_dir)
        if args.stderr_file:
            args.stderr_file = LocalStorage.resolve_path(args.stderr_file, self.script_dir)
        return args

    def find_shell(self) -> str:
        for name in ("pwsh.exe", "pwsh", "powershell.exe", "powershell"):
            found = shutil.which(name)
            if found:
                return found
        raise RuntimeError("PowerShell was not found in PATH.")

    def send_note(self) -> int:
        if self.args.note_stdin:
            note_text = sys.stdin.read().strip()
        else:
            note_text = self.args.note_text.strip()
        if not note_text:
            print("[note] No note text provided.", file=sys.stderr)
            return 1

        LocalStorage.append_jsonl(
            self.args.rounds_file,
            {
                "round_id": f"note-{self.utc_now()}",
                "timestamp_utc": self.utc_now(),
                "type": "note",
                "cwd": self.args.cwd,
                "challenge_id": self.args.challenge_id,
                "note": note_text,
            },
        )
        return self._emit(
            self.client.ingest_human_note(self.args.challenge_id, note_text, actor=self.args.actor)
        )

    def send_command_result(self) -> int:
        command = self.args.command_text.strip()
        if not command or CommandFilter.is_trivial(command):
            return 0
        stdout = self.args.stdout_file.read_text(encoding="utf-8") if self.args.stdout_file else ""
        stderr = self.args.stderr_file.read_text(encoding="utf-8") if self.args.stderr_file else ""
        argv = shlex.split(command)
        LocalStorage.append_jsonl(
            self.args.rounds_file,
            {
                "round_id": f"cmd-{self.utc_now()}",
                "timestamp_utc": self.utc_now(),
                "type": "command_result",
                "cwd": self.args.cwd,
                "challenge_id": self.args.challenge_id,
                "command": argv,
                "exit_code": self.args.exit_code,
            },
        )
        return self._emit(
            self.client.ingest_command_result(
                self.args.challenge_id,
                argv,
                exit_code=self.args.exit_code,
                stdout=stdout,
                stderr=stderr,
                actor=self.args.actor,
            )
        )

    def launch_shell(self) -> int:
        shell = self.args.shell or self.find_shell()
        profile_script = self.script_dir / "core" / "human_cli" / "note_shell_profile.ps1"
        main_script = self.script_dir / "main.py"
        command_text = (
            f". '{profile_script}' "
            f"-PythonExe '{sys.executable}' "
            f"-HelperScript '{main_script.resolve()}' "
            f"-WorkspaceDir '{self.script_dir}'"
        )
        return subprocess.call(
            [shell, "-NoLogo", "-NoExit", "-ExecutionPolicy", "Bypass", "-Command", command_text]
        )

    def run(self) -> int:
        try:
            if self.args.send_note:
                return self.send_note()
            if self.args.send_command_result or self.args.auto_note:
                return self.send_command_result()
            return self.launch_shell()
        except MemoryCoreHTTPError as exc:
            if exc.code == "revision_conflict":
                print(
                    "[note] Memory Core revision conflict; reload memory and retry.",
                    file=sys.stderr,
                )
            else:
                print(f"[note] {exc}", file=sys.stderr)
            return 3

    def _emit(self, body: dict[str, object]) -> int:
        if self.args.json:
            print(json.dumps(body, ensure_ascii=False, indent=2))
        else:
            before = body.get("revision_before")
            after = body.get("revision_after")
            source_id = body.get("source_id")
            print(f"[note] Ingested into Memory Core: {before} -> {after} ({source_id})")
        return 0
