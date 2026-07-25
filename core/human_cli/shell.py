from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from core.note_ai.command_filter import CommandFilter
from core.note_ai.llm_client import LLMClient
from core.note_ai.storage import Storage


class PowerShellNoteApp:
    """應用程式核心邏輯與 Shell 控制器"""

    DEFAULT_BASE_URL = "https://api.openai.com/v1"
    DEFAULT_MODEL = "gpt-4.1-mini"
    DEFAULT_SYSTEM_PROMPT = (
        "You are a concise note-taking assistant. Turn the user's terminal note "
        "and recent command history into a clear, structured rolling technical note. "
        "Keep factual uncertainty visible."
    )
    AUTO_NOTE_SYSTEM_PROMPT = (
        "You are an auto note-taking assistant inside a live terminal session.\n"
        "After each command the user runs, you evaluate whether it is note-worthy and update the rolling note accordingly.\n\n"
        "Guidelines:\n"
        "1. Only add genuinely useful information for the current task context.\n"
        "2. Ignore trivial navigation, editor opening, package installs, and typos.\n"
        "3. Do NOT invent findings not supported by the command context.\n"
        "4. Keep uncertainty visible -- distinguish observed facts from inferences.\n"
        "5. Merge new information with existing content; preserve all previously recorded useful info.\n"
        "6. If the command appears irrelevant, return the existing note unchanged.\n"
        "7. Structure the note with clear sections for easy scanning.\n\n"
        "Output the complete updated rolling note."
    )

    def __init__(self, script_dir: Path) -> None:
        self.script_dir = script_dir
        Storage.load_dotenv(self.script_dir / ".env")
        self.args = self._parse_args()
        self.client = LLMClient(
            base_url=self.args.base_url,
            api_key=self.args.api_key,
            model=self.args.model,
            timeout=self.args.timeout,
        )

    @staticmethod
    def utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _parse_args(self) -> argparse.Namespace:
        parser = argparse.ArgumentParser(
            description="Launch a real interactive PowerShell with a `note` command."
        )
        parser.add_argument("--send-note", action="store_true", help=argparse.SUPPRESS)
        parser.add_argument("--auto-note", action="store_true", help=argparse.SUPPRESS)
        parser.add_argument("--command-text", default="", help=argparse.SUPPRESS)
        parser.add_argument("--note-text", default="", help=argparse.SUPPRESS)
        parser.add_argument("--note-stdin", action="store_true", help=argparse.SUPPRESS)
        parser.add_argument("--cwd", default=str(Path.cwd()), help=argparse.SUPPRESS)
        parser.add_argument("--base-url", default=os.getenv("AI_BASE_URL", self.DEFAULT_BASE_URL))
        parser.add_argument("--model", default=os.getenv("AI_MODEL", self.DEFAULT_MODEL))
        parser.add_argument("--api-key", default=os.getenv("AI_API_KEY"))
        parser.add_argument("--system-prompt", default=os.getenv("AI_SYSTEM_PROMPT", self.DEFAULT_SYSTEM_PROMPT))
        parser.add_argument("--skill", default=os.getenv("AI_SKILL", "ctf-rolling-notes"))
        parser.add_argument("--skills-dir", type=Path, default=Path(os.getenv("AI_SKILLS_DIR", "skills")))
        parser.add_argument("--rounds-window", type=int, default=int(os.getenv("AI_ROUNDS_WINDOW", "10")))
        parser.add_argument("--rounds-file", type=Path, default=Path(os.getenv("AI_ROUNDS_FILE", "rounds.jsonl")))
        parser.add_argument(
            "--pending-rounds-file",
            type=Path,
            default=Path(os.getenv("AI_PENDING_ROUNDS_FILE", "pending_rounds.jsonl")),
        )
        parser.add_argument("--notes-file", type=Path, default=Path(os.getenv("AI_NOTES_FILE", "ai_notes.md")))
        parser.add_argument("--shell", default=os.getenv("AI_SHELL"))
        parser.add_argument("--timeout", type=int, default=int(os.getenv("AI_TIMEOUT", "60")))
        parser.add_argument("--save-request", type=Path, help="Save the latest request JSON to this path.")
        parser.add_argument("--save-response", type=Path, help="Save the latest response JSON to this path.")
        parser.add_argument("--no-send", action="store_true", help="Do not call the API; print captured notes only.")
        args = parser.parse_args()

        args.skills_dir = Storage.resolve_path(args.skills_dir, self.script_dir)
        args.rounds_file = Storage.resolve_path(args.rounds_file, self.script_dir)
        args.pending_rounds_file = Storage.resolve_path(args.pending_rounds_file, self.script_dir)
        args.notes_file = Storage.resolve_path(args.notes_file, self.script_dir)
        if args.save_request:
            args.save_request = Storage.resolve_path(args.save_request, self.script_dir)
        if args.save_response:
            args.save_response = Storage.resolve_path(args.save_response, self.script_dir)

        return args

    def find_powershell(self) -> str:
        for name in ("pwsh.exe", "pwsh", "powershell.exe", "powershell"):
            found = shutil.which(name)
            if found:
                return found
        raise RuntimeError("PowerShell was not found in PATH.")

    def load_skill(self) -> str:
        if not self.args.skill:
            return ""

        skill_path = self.args.skills_dir / self.args.skill / "SKILL.md"
        if not skill_path.is_file():
            print(f"[note] Skill not found: {skill_path}", file=sys.stderr)
            return ""

        return skill_path.read_text(encoding="utf-8")

    def auto_note(self) -> int:
        command = self.args.command_text.strip()
        if not command or CommandFilter.is_trivial(command) or command.startswith("note"):
            return 0

        skill_text = self.load_skill()
        current_note = Storage.read_text_if_exists(self.args.notes_file)
        recent_rounds = Storage.load_recent_jsonl(self.args.rounds_file, self.args.rounds_window)

        user_content = {
            "instruction": "Auto-update the rolling note based on this terminal command.",
            "working_directory": self.args.cwd,
            "captured_at_utc": self.utc_now(),
            "executed_command": command,
            "current_rolling_note": current_note,
            "recent_rounds": recent_rounds,
        }

        messages = [{"role": "system", "content": self.AUTO_NOTE_SYSTEM_PROMPT}]
        if skill_text:
            messages.append({"role": "system", "content": "Use this Skill specification when updating the note:\n\n" + skill_text})
        messages.append({"role": "user", "content": json.dumps(user_content, ensure_ascii=False, indent=2)})

        payload = {"model": self.args.model, "messages": messages}

        if self.args.save_request:
            Storage.save_json(self.args.save_request, payload)

        if self.args.no_send:
            return 0

        if not self.args.api_key:
            return 2

        try:
            response = self.client.post_chat_completion(payload)
        except RuntimeError as exc:
            print(f"[autonote] {exc}", file=sys.stderr)
            return 3

        if self.args.save_response:
            Storage.save_json(self.args.save_response, response)

        ai_note = LLMClient.extract_message(response)
        if ai_note and len(ai_note) > 50:
            Storage.write_text(self.args.notes_file, ai_note)

        return 0

    def send_note(self) -> int:
        note_text = self.args.note_text
        if self.args.note_stdin:
            note_text = sys.stdin.read().strip()

        if not note_text:
            print("[note] No note text provided.", file=sys.stderr)
            return 1

        note_round = {
            "round_id": f"note-{self.utc_now()}",
            "timestamp_utc": self.utc_now(),
            "type": "note",
            "cwd": self.args.cwd,
            "note": note_text,
        }
        Storage.append_jsonl(self.args.rounds_file, note_round)

        skill_text = self.load_skill()
        current_note = Storage.read_text_if_exists(self.args.notes_file)
        recent_rounds = Storage.load_recent_jsonl(self.args.rounds_file, self.args.rounds_window)

        user_content = {
            "instruction": "Update the rolling note using the new note and recent terminal command rounds.",
            "working_directory": self.args.cwd,
            "captured_at_utc": self.utc_now(),
            "new_note": note_text,
            "current_rolling_note": current_note,
            "recent_rounds": recent_rounds,
        }

        messages = [{"role": "system", "content": self.args.system_prompt}]
        if skill_text:
            messages.append({"role": "system", "content": "Use this Skill specification when updating the note:\n\n" + skill_text})
        messages.append({"role": "user", "content": json.dumps(user_content, ensure_ascii=False, indent=2)})

        payload = {"model": self.args.model, "messages": messages}

        if self.args.save_request:
            Storage.save_json(self.args.save_request, payload)

        if self.args.no_send:
            print("[note] --no-send enabled. Captured note:")
            print(note_text)
            return 0

        if not self.args.api_key:
            print("[note] Missing AI_API_KEY. Set it in .env, or pass --api-key.", file=sys.stderr)
            Storage.append_jsonl(self.args.pending_rounds_file, {**note_round, "pending_reason": "missing_api_key"})
            return 2

        try:
            response = self.client.post_chat_completion(payload)
        except RuntimeError as exc:
            print(f"[note] {exc}", file=sys.stderr)
            Storage.append_jsonl(self.args.pending_rounds_file, {**note_round, "pending_reason": str(exc)})
            return 3

        if self.args.save_response:
            Storage.save_json(self.args.save_response, response)

        ai_note = LLMClient.extract_message(response)
        print("\n[note] Updated rolling note:")
        print("-" * 40)
        print(ai_note)
        print("-" * 40)

        Storage.write_text(self.args.notes_file, ai_note)
        print(f"[note] Saved to {self.args.notes_file}")
        return 0

    def launch_shell(self) -> int:
        shell = self.args.shell or self.find_powershell()
        profile_script = self.script_dir / "core" / "human_cli" / "note_shell_profile.ps1"
        main_script = self.script_dir / "main.py"
        command_text = (
            f". '{profile_script}' "
            f"-PythonExe '{sys.executable}' "
            f"-HelperScript '{main_script.resolve()}' "
            f"-WorkspaceDir '{self.script_dir}'"
        )

        command = [
            shell,
            "-NoLogo",
            "-NoExit",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command_text,
        ]

        return subprocess.call(command)

    def run(self) -> int:
        if self.args.send_note:
            return self.send_note()
        if self.args.auto_note:
            return self.auto_note()
        return self.launch_shell()
