from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from core.note_ai.memory_bridge import NoteBridge
from core.note_ai.storage import Storage


class PowerShellNoteApp:
    """應用程式核心邏輯與 Shell 控制器

    Human CLI 現在透過 Memory Core API 進行所有筆記寫入與查詢。
    550B 模型的 Summarizer 由 Memory Core 內部呼叫，CLI 不再直接呼叫 LLM。
    """

    def __init__(self, script_dir: Path) -> None:
        self.script_dir = script_dir
        Storage.load_dotenv(self.script_dir / ".env")
        self.args = self._parse_args()
        self.bridge = NoteBridge(
            memory_core_url=self.args.memory_core_url,
            challenge_id=self.args.challenge,
        )

    def _parse_args(self) -> argparse.Namespace:
        parser = argparse.ArgumentParser(
            description="Launch a real interactive PowerShell with a `note` command."
        )
        parser.add_argument("--send-note", action="store_true", help=argparse.SUPPRESS)
        parser.add_argument("--auto-note", action="store_true", help=argparse.SUPPRESS)
        parser.add_argument("--query", action="store_true", help=argparse.SUPPRESS)
        parser.add_argument("--list-challenges", action="store_true", help=argparse.SUPPRESS)
        parser.add_argument("--capture", action="store_true", help=argparse.SUPPRESS)
        parser.add_argument("--read", action="store_true", help=argparse.SUPPRESS)
        parser.add_argument("--guess", action="store_true", help=argparse.SUPPRESS)
        parser.add_argument("--command-text", default="", help=argparse.SUPPRESS)
        parser.add_argument("--note-text", default="", help=argparse.SUPPRESS)
        parser.add_argument("--note-stdin", action="store_true", help=argparse.SUPPRESS)
        parser.add_argument("--query-challenge", default="", help=argparse.SUPPRESS)
        parser.add_argument("--query-layer", default="", help=argparse.SUPPRESS)
        parser.add_argument("--query-section", default="", help=argparse.SUPPRESS)
        parser.add_argument("--file-path", default="", help=argparse.SUPPRESS)
        parser.add_argument("--cwd", default=str(Path.cwd()), help=argparse.SUPPRESS)
        parser.add_argument("--guess-count", type=int, default=3, help=argparse.SUPPRESS)
        parser.add_argument("--guess-seed", type=int, default=None, help=argparse.SUPPRESS)
        parser.add_argument("--only-untried", action="store_true", default=True, help=argparse.SUPPRESS)
        parser.add_argument(
            "--memory-core-url",
            default=os.getenv("MEMORY_CORE_URL", "http://127.0.0.1:8777"),
        )
        parser.add_argument(
            "--challenge",
            default=os.getenv("MEMORY_CORE_CHALLENGE", "human-cli-notes"),
        )
        parser.add_argument("--auto", action="store_true", help="Auto mode: run command + capture + show diff in one round")
        parser.add_argument("--no-diff", action="store_true", help="Suppress diff output")
        parser.add_argument("--one-line", action="store_true", help="Compact one-line diff format")
        parser.add_argument("--shell", default=os.getenv("AI_SHELL"))
        parser.add_argument("--json", action="store_true", help="Output in JSON format")
        args = parser.parse_args()

        return args

    def find_powershell(self) -> str:
        for name in ("pwsh.exe", "pwsh", "powershell.exe", "powershell"):
            found = shutil.which(name)
            if found:
                return found
        raise RuntimeError("PowerShell was not found in PATH.")

    def auto_note(self) -> int:
        """Ingest a terminal command into Memory Core as a CommandResultInput."""
        command = self.args.command_text.strip()
        if not command or command.startswith("note"):
            return 0

        # Skip trivial commands (same filter as before)
        from core.note_ai.command_filter import CommandFilter
        if CommandFilter.is_trivial(command):
            return 0

        try:
            resp = self.bridge.ingest_command(
                command=command,
                cwd=self.args.cwd,
                exit_code=0,
            )
            if self.args.json:
                print(json.dumps(resp, ensure_ascii=False, indent=2))
            else:
                rev = resp.get("revision_after", "?")
                source_id = resp.get("source_id", "?")
                diff = resp.get("diff", {})
                summary = resp.get("summary", "")
                print(f"[autonote] rev {rev}  source: {source_id}")
                if summary:
                    print(f"  Summary: {summary}")
                if not self.args.no_diff:
                    self._render_diff(diff)
        except RuntimeError as exc:
            print(f"[autonote] {exc}", file=sys.stderr)
            return 3

        return 0

    def send_note(self) -> int:
        """Ingest a human note into Memory Core as a NoteInput."""
        note_text = self.args.note_text
        if self.args.note_stdin:
            note_text = sys.stdin.read().strip()

        if not note_text:
            print("[note] No note text provided.", file=sys.stderr)
            return 1

        try:
            resp = self.bridge.ingest_note(
                note_text=note_text,
                cwd=self.args.cwd,
            )
            print("[note] Ingested into Memory Core.")
            if self.args.json:
                print(json.dumps(resp, ensure_ascii=False, indent=2))
            else:
                rev = resp.get("revision_after", "?")
                source_id = resp.get("source_id", "?")
                diff = resp.get("diff", {})
                summary = resp.get("summary", "")
                print(f"  Revision: {rev}  source: {source_id}")
                if summary:
                    print(f"  Summary: {summary}")
                if not self.args.no_diff:
                    self._render_diff(diff)
        except RuntimeError as exc:
            print(f"[note] {exc}", file=sys.stderr)
            return 3

        return 0

    def query_challenge(self) -> int:
        """Query a challenge's memory from Memory Core."""
        challenge_id = self.args.query_challenge or self.args.challenge

        try:
            resp = self.bridge.read_memory(
                challenge_id,
                layer=self.args.query_layer or None,
                section=self.args.query_section or None,
            )
            if self.args.json:
                print(json.dumps(resp, ensure_ascii=False, indent=2))
            else:
                self._render_memory(resp)
        except RuntimeError as exc:
            print(f"[query] {exc}", file=sys.stderr)
            return 3

        return 0

    def list_challenges(self) -> int:
        """List all known challenge IDs from Memory Core."""
        try:
            challenges = self.bridge.list_challenges()
            if self.args.json:
                print(json.dumps(challenges, ensure_ascii=False, indent=2))
            else:
                if not challenges:
                    print("[query] No challenges found.")
                else:
                    print(f"[query] Known challenges ({len(challenges)}):")
                    for cid in challenges:
                        print(f"  - {cid}")
        except RuntimeError as exc:
            print(f"[query] {exc}", file=sys.stderr)
            return 3

        return 0

    def capture_command(self) -> int:
        """Execute a command, capture output, and ingest into Memory Core.

        Runs the command, displays output to terminal, then sends to Memory Core
        as a CommandResultInput. Returns the ingest summary.
        """
        command = self.args.command_text.strip()
        if not command:
            print("[capture] No command provided.", file=sys.stderr)
            return 1

        # Execute the command and capture output
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=self.args.cwd,
            )
            stdout = result.stdout
            stderr = result.stderr
            exit_code = result.returncode
        except Exception as exc:
            print(f"[capture] Command execution failed: {exc}", file=sys.stderr)
            return 2

        # Display the output to terminal
        if stdout:
            print(stdout, end="")
        if stderr:
            print(stderr, end="", file=sys.stderr)

        # Ingest into Memory Core
        try:
            resp = self.bridge.ingest_command(
                command=command,
                cwd=self.args.cwd,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
            )
            if self.args.json:
                print(json.dumps(resp, ensure_ascii=False, indent=2))
            else:
                rev = resp.get("revision_after", "?")
                source_id = resp.get("source_id", "?")
                diff = resp.get("diff", {})
                summary = resp.get("summary", "")
                print(f"\n[capture] Ingested into Memory Core (rev {rev}  source: {source_id})")
                if summary:
                    print(f"  Summary: {summary}")
                if not self.args.no_diff:
                    self._render_diff(diff)
        except RuntimeError as exc:
            print(f"\n[capture] Ingest failed: {exc}", file=sys.stderr)
            return 3

        return 0

    def read_state(self) -> int:
        """Read a state.yaml file and render it for terminal display."""
        file_path = self.args.file_path
        if not file_path:
            # Default to runs/<challenge>/state.yaml
            challenge_id = self.args.challenge
            file_path = str(self.script_dir / "runs" / challenge_id / "state.yaml")

        path = Path(file_path)
        if not path.exists():
            print(f"[read] File not found: {file_path}", file=sys.stderr)
            return 1

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except Exception as exc:
            print(f"[read] Failed to read YAML: {exc}", file=sys.stderr)
            return 2

        if self.args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            self._render_state_yaml(data)

        return 0

    def _render_state_yaml(self, data: dict) -> None:
        """Render state.yaml content in human-readable format."""
        challenge = data.get("challenge", {})
        revision = data.get("revision", 0)
        print(f"Challenge: {challenge.get('id', '?')} (rev {revision})")
        print(f"  Title: {challenge.get('title', '')}")
        print(f"  Category: {challenge.get('category', 'generic')}")
        print(f"  Template: {challenge.get('template', 'generic')}@{challenge.get('template_version', 1)}")
        if challenge.get("summary"):
            print(f"  Summary: {challenge.get('summary', '')}")
        print()

        facts = data.get("fact", {})
        if facts:
            print("Facts:")
            for section, items in facts.items():
                if items:
                    print(f"  [{section}]")
                    for item in items:
                        print(f"    [{item.get('id', '?')}] {item.get('text', '')}")
                        if item.get("evidence"):
                            print(f"      evidence: {', '.join(item['evidence'])}")
            print()

        assumptions = data.get("assumption", {})
        if assumptions:
            print("Assumptions:")
            for section, items in assumptions.items():
                if items:
                    print(f"  [{section}]")
                    for item in items:
                        print(f"    [{item.get('id', '?')}] {item.get('text', '')}")
            print()

        guessing = data.get("guessing", {})
        if guessing:
            print(f"Guessing: {guessing.get('catalog', '?')}")
            state = guessing.get("state", {})
            tried = [k for k, v in state.items() if v.get("tried")]
            untried = [k for k, v in state.items() if not v.get("tried")]
            if tried:
                print(f"  Tried: {', '.join(tried)}")
            if untried:
                print(f"  Untried: {', '.join(untried[:10])}")
                if len(untried) > 10:
                    print(f"    ... and {len(untried) - 10} more")

    def guess_techniques(self) -> int:
        """Get guessing technique suggestions from Memory Core."""
        challenge_id = self.args.challenge
        count = self.args.guess_count
        seed = self.args.guess_seed
        only_untried = self.args.only_untried

        try:
            resp = self.bridge.guess(
                challenge_id,
                count=count,
                only_untried=only_untried,
                seed=seed,
            )
            if self.args.json:
                print(json.dumps(resp, ensure_ascii=False, indent=2))
            else:
                catalog = resp.get("catalog", "?")
                candidates = resp.get("candidates", [])
                print(f"[guess] Techniques from {catalog}:")
                for i, c in enumerate(candidates, 1):
                    tried = " (tried)" if c.get("tried") else ""
                    print(f"  {i}. {c.get('id', '?')}: {c.get('title', '')}{tried}")
                    if c.get("tags"):
                        print(f"     tags: {', '.join(c['tags'])}")
        except RuntimeError as exc:
            print(f"[guess] {exc}", file=sys.stderr)
            return 3

        return 0

    def _render_memory(self, resp: dict) -> None:
        """Render MemoryReadResponse in human-readable format."""
        challenge = resp.get("challenge", {})
        revision = resp.get("revision", 0)
        print(f"Challenge: {challenge.get('id', '?')} (rev {revision})")
        print(f"  Title: {challenge.get('title', '')}")
        print(f"  Template: {challenge.get('template', 'generic')}@{challenge.get('template_version', 1)}")
        print()

        facts = resp.get("fact", {})
        if facts:
            print("Facts:")
            for section, items in facts.items():
                for item in items:
                    print(f"  [{item.get('id', '?')}] {item.get('text', '')}")
                    if item.get("evidence"):
                        print(f"    evidence: {', '.join(item['evidence'])}")
            print()

        assumptions = resp.get("assumption", {})
        if assumptions:
            print("Assumptions:")
            for section, items in assumptions.items():
                for item in items:
                    print(f"  [{item.get('id', '?')}] {item.get('text', '')}")
            print()

        guessing = resp.get("guessing")
        if guessing:
            print(f"Guessing: {guessing.get('catalog', '?')}")
            state = guessing.get("state", {})
            tried = [k for k, v in state.items() if v.get("tried")]
            untried = [k for k, v in state.items() if not v.get("tried")]
            if tried:
                print(f"  Tried: {', '.join(tried)}")
            if untried:
                print(f"  Untried: {', '.join(untried[:10])}")
                if len(untried) > 10:
                    print(f"    ... and {len(untried) - 10} more")

    _DIFF_LABELS = {
        "fact_added": "+",
        "fact_updated": "~",
        "fact_removed": "-",
        "assumption_added": "+",
        "assumption_updated": "~",
        "assumption_removed": "-",
        "promoted": "↑",
        "demoted": "↓",
    }

    # map diff key → human-readable layer name
    _DIFF_LAYER = {
        "fact_added": "Fact",
        "fact_updated": "Fact",
        "fact_removed": "Fact",
        "assumption_added": "Assumption",
        "assumption_updated": "Assumption",
        "assumption_removed": "Assumption",
        "promoted": "Ass→Fact",
        "demoted": "Fact→Ass",
    }

    def _render_diff(self, diff: dict) -> None:
        """Render a MemoryDiff dict in standard multi-line format."""
        if self.args.one_line:
            self._render_diff_oneline(diff)
            return

        print("  Diff:")
        total = 0
        for key in ("fact_added", "fact_updated", "fact_removed",
                    "assumption_added", "assumption_updated", "assumption_removed",
                    "promoted", "demoted"):
            items = diff.get(key, [])
            if not items:
                continue
            prefix = self._DIFF_LABELS.get(key, "?")
            layer = self._DIFF_LAYER.get(key, "")
            total += len(items)
            for item_id in items:
                print(f"    {prefix} {item_id:<8} {layer}")

        if diff.get("template_changed"):
            tc = diff["template_changed"]
            print(f"    * template  {tc.get('from', '?')} → {tc.get('to', '?')}")
            total += 1

        if total == 0:
            print("    (no changes)")

    def _render_diff_oneline(self, diff: dict) -> None:
        """Render diff as a single compact line."""
        parts = []
        for key, label in [("fact_added", "+F"), ("fact_updated", "~F"), ("fact_removed", "-F"),
                           ("assumption_added", "+A"), ("assumption_updated", "~A"),
                           ("assumption_removed", "-A"),
                           ("promoted", "↑"), ("demoted", "↓")]:
            n = len(diff.get(key, []))
            if n:
                parts.append(f"{label}{n}")
        if diff.get("template_changed"):
            parts.append("*template")
        if not parts:
            print("  Diff: (no changes)")
        else:
            print(f"  Diff: {' '.join(parts)}")

    def auto_mode(self) -> int:
        """Full auto round: run a command, capture output, ingest with diff.

        One round = execute → ingest → show diff.  The diff is computed by the
        server on-the-fly (never persisted) and streamed back in the response.
        """
        command = self.args.command_text.strip()
        if not command:
            print("[auto] No command provided.", file=sys.stderr)
            return 1

        # 1. Execute
        print(f"[auto] $ {command}")
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=self.args.cwd,
            )
        except Exception as exc:
            print(f"[auto] Execution failed: {exc}", file=sys.stderr)
            return 2

        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)

        # 2. Ingest into Memory Core
        try:
            resp = self.bridge.ingest_command(
                command=command,
                cwd=self.args.cwd,
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
            )
        except RuntimeError as exc:
            print(f"[auto] Ingest failed: {exc}", file=sys.stderr)
            return 3

        # 3. Show result with diff
        if self.args.json:
            print(json.dumps(resp, ensure_ascii=False, indent=2))
        else:
            rev = resp.get("revision_after", "?")
            source_id = resp.get("source_id", "?")
            diff = resp.get("diff", {})
            summary = resp.get("summary", "")
            print(f"[auto] Ingested into Memory Core (rev {rev}  source: {source_id})")
            if summary:
                print(f"  Summary: {summary}")
            if not self.args.no_diff:
                self._render_diff(diff)

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
        if self.args.auto:
            return self.auto_mode()
        if self.args.send_note:
            return self.send_note()
        if self.args.auto_note:
            return self.auto_note()
        if self.args.query:
            return self.query_challenge()
        if self.args.list_challenges:
            return self.list_challenges()
        if self.args.capture:
            return self.capture_command()
        if self.args.read:
            return self.read_state()
        if self.args.guess:
            return self.guess_techniques()
        return self.launch_shell()
