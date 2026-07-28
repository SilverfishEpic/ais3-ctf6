"""Codex CLI adapter.

Codex is driven through the documented non-interactive CLI JSONL surface.  ``session=new``
starts a fresh ``codex exec`` for every turn.  ``session=same`` starts a persistent Codex
thread on the first turn and resumes that exact thread id on later turns.

Codex receives read-only memory tools through a scoped stdio MCP server when the selected
memory policy enables the skill. The subprocess is launched with Codex-native approval
gating, read-only sandboxing, isolated user/project policy sources, and scrubbed Memory Core
provider credentials.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agent_bridge.envelope import build_envelope
from agent_bridge.errors import BridgeError
from agent_bridge.normalize_codex import normalize_codex
from agent_bridge.protocols import RunResult

BOARD_SEPARATOR = "\n\n---\n\n"
SECRET_ENV_NAMES = frozenset(
    {
        "AIS3_API_KEY",
        "AIS3_BASE_URL",
        "AIS3_SUMMARIZER_MODEL",
        "AIS3_TIMEOUT",
        "MEMORY_CORE_RUNS_DIR",
        "MEMORY_CORE_TEMPLATES_DIR",
    }
)


@dataclass(frozen=True)
class CodexMemoryMCP:
    api_url: str
    challenge: str
    policy: str
    timeout: float
    guess_seed: int | None = None


class CodexAdapter:
    """``AgentAdapter`` implementation backed by ``codex exec --json``."""

    def __init__(
        self,
        *,
        workspace: str | Path,
        executable: str | None = None,
        model: str | None = None,
        approval_policy: Literal["untrusted", "never", "on-request"] = "untrusted",
        sandbox: str = "read-only",
        session_kind: Literal["same", "new"] = "new",
        ephemeral: bool | None = None,
        memory_mcp: CodexMemoryMCP | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.executable = executable or os.environ.get("CTFMEM_CODEX_BIN") or "codex"
        self.model = model
        self.approval_policy = approval_policy
        self.sandbox = sandbox
        self.session_kind = session_kind
        self.memory_mcp = memory_mcp
        # A same-session run must persist Codex's thread so later turns can resume it.
        self.ephemeral = session_kind == "new" if ephemeral is None else ephemeral
        self._turn = 0
        self._process: asyncio.subprocess.Process | None = None
        self._cancel_requested = False
        self._session_id: str | None = None

    async def start(self, prompt: str, *, board: str | None = None) -> RunResult:
        return await self._run(prompt, board, first=True)

    async def continue_run(self, prompt: str, *, board: str | None = None) -> RunResult:
        return await self._run(prompt, board, first=False)

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

    async def _run(self, prompt: str, board: str | None, *, first: bool) -> RunResult:
        self._turn += 1
        full_prompt = f"{board}{BOARD_SEPARATOR}{prompt}" if board else prompt
        argv = self._argv(full_prompt, resume=not first and self.session_kind == "same")

        rows: list[dict[str, object]] = []
        stderr = ""
        try:
            self._process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(self.workspace),
                env=self._subprocess_env(),
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
        session_id = run.run_meta.get("session_id")
        if isinstance(session_id, str):
            self._session_id = session_id
        if self.session_kind == "same" and self._session_id is None:
            raise BridgeError("codex exec did not report a thread_id; cannot continue same session")
        envelope = build_envelope(
            run,
            provider="openai-codex",
            model=self.model,
            run_id=f"codex-turn-{self._turn}",
            session_id=self._session_id,
        )
        return RunResult(envelope=envelope, run=run)

    def _argv(self, prompt: str, *, resume: bool = False) -> list[str]:
        if resume:
            if self._session_id is None:
                raise BridgeError("cannot resume Codex before a thread_id is known")
            argv = [
                self.executable,
                "--ask-for-approval",
                self.approval_policy,
                "exec",
                "resume",
                "--json",
                "--ignore-user-config",
                "--ignore-rules",
            ]
            argv += self._mcp_config_args()
            if self.model:
                argv += ["--model", self.model]
            argv += [self._session_id, prompt]
            return argv

        argv = [
            self.executable,
            "--ask-for-approval",
            self.approval_policy,
            "exec",
            "--json",
            "--ignore-user-config",
            "--ignore-rules",
            "--sandbox",
            self.sandbox,
            "--cd",
            str(self.workspace),
        ]
        argv += self._mcp_config_args()
        if self.ephemeral:
            argv.append("--ephemeral")
        if self.model:
            argv += ["--model", self.model]
        argv.append(prompt)
        return argv

    def _mcp_config_args(self) -> list[str]:
        if self.memory_mcp is None:
            return []
        mcp_args = [
            "-m",
            "agent_bridge.memory_mcp",
            "--api-url",
            self.memory_mcp.api_url,
            "--challenge",
            self.memory_mcp.challenge,
            "--policy",
            self.memory_mcp.policy,
            "--timeout",
            str(self.memory_mcp.timeout),
        ]
        if self.memory_mcp.guess_seed is not None:
            mcp_args += ["--guess-seed", str(self.memory_mcp.guess_seed)]
        return [
            "-c",
            "mcp_servers.memory.command=" + json.dumps(sys.executable),
            "-c",
            "mcp_servers.memory.args=" + json.dumps(mcp_args),
            "-c",
            'mcp_servers.memory.enabled_tools=["memory_show","memory_query","memory_guess"]',
            "-c",
            "mcp_servers.memory.default_tools_approval_mode=\"auto\"",
        ]

    def _subprocess_env(self) -> dict[str, str]:
        env = dict(os.environ)
        for name in SECRET_ENV_NAMES:
            env.pop(name, None)
        return env


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
