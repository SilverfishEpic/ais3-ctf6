"""Codex CLI adapter.

MVP support is intentionally narrow: one non-interactive ``codex exec --json`` turn per
adapter call.  Same-session continuation, Codex MCP memory tools, and Codex-specific tool
policy parity are follow-up work tracked in ADR 0003.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path

from agent_bridge.envelope import build_envelope
from agent_bridge.errors import BridgeError
from agent_bridge.normalize_codex import normalize_codex
from agent_bridge.protocols import RunResult

BOARD_SEPARATOR = "\n\n---\n\n"


class CodexAdapter:
    """``AgentAdapter`` implementation backed by ``codex exec --json``."""

    session_kind = "new"

    def __init__(
        self,
        *,
        workspace: str | Path,
        executable: str | None = None,
        model: str | None = None,
        sandbox: str = "read-only",
        ephemeral: bool = True,
    ) -> None:
        self.workspace = Path(workspace)
        self.executable = executable or os.environ.get("CTFMEM_CODEX_BIN") or "codex"
        self.model = model
        self.sandbox = sandbox
        self.ephemeral = ephemeral
        self._turn = 0
        self._process: asyncio.subprocess.Process | None = None
        self._cancel_requested = False

    async def start(self, prompt: str, *, board: str | None = None) -> RunResult:
        return await self._run(prompt, board)

    async def continue_run(self, prompt: str, *, board: str | None = None) -> RunResult:
        return await self._run(prompt, board)

    async def cancel(self) -> None:
        self._cancel_requested = True
        if self._process is not None and self._process.returncode is None:
            self._process.terminate()

    async def close(self) -> None:
        if self._process is not None and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=2)
            except TimeoutError:
                self._process.kill()
                await self._process.wait()

    async def _run(self, prompt: str, board: str | None) -> RunResult:
        self._turn += 1
        full_prompt = f"{board}{BOARD_SEPARATOR}{prompt}" if board else prompt
        argv = self._argv(full_prompt)

        rows: list[dict[str, object]] = []
        stderr = ""
        try:
            self._process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(self.workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            resolved = shutil.which(self.executable)
            hint = (
                f"Codex executable {self.executable!r} was not found."
                if resolved is None
                else f"Codex executable could not be started: {resolved}"
            )
            raise BridgeError(hint) from exc

        assert self._process.stdout is not None
        assert self._process.stderr is not None

        stdout_data, stderr_data = await self._process.communicate()
        stderr = stderr_data.decode("utf-8", errors="replace")
        stdout = stdout_data.decode("utf-8", errors="replace")
        rows = _parse_jsonl(stdout)

        if self._cancel_requested:
            rows.append(
                {
                    "type": "item.completed",
                    "item": {
                        "id": f"codex-interrupted-{self._turn}",
                        "type": "command_execution",
                        "status": "interrupted",
                        "output": "Codex run was interrupted by Agent Bridge.",
                    },
                }
            )
            self._cancel_requested = False

        if self._process.returncode not in (0, None) and not rows:
            detail = stderr.strip() or f"exit code {self._process.returncode}"
            raise BridgeError(f"codex exec failed: {detail[:800]}")

        run = normalize_codex(rows)
        envelope = build_envelope(
            run,
            provider="openai-codex",
            model=self.model,
            run_id=f"codex-turn-{self._turn}",
            session_id=run.run_meta.get("session_id"),
        )
        return RunResult(envelope=envelope, run=run)

    def _argv(self, prompt: str) -> list[str]:
        argv = [
            self.executable,
            "exec",
            "--json",
            "--sandbox",
            self.sandbox,
            "--cd",
            str(self.workspace),
        ]
        if self.ephemeral:
            argv.append("--ephemeral")
        if self.model:
            argv += ["--model", self.model]
        argv.append(prompt)
        return argv


def _parse_jsonl(text: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            rows.append(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": line,
                    },
                }
            )
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows
