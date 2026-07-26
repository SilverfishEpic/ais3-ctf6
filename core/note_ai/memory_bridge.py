"""Bridge between Human CLI notes and Memory Core ingest.

Relays terminal commands and human notes to Memory Core's ``POST /v1/challenges/{id}/ingest``
endpoint as ``IngestEnvelope`` payloads.  This module does **not** decide how the ingest
response is consumed — that is the caller's responsibility (see ``human_cli/shell.py``).

Environment variables
---------------------
MEMORY_CORE_URL (str)        : Base URL of the Memory Core server.
                               Default: http://127.0.0.1:8777
MEMORY_CORE_CHALLENGE (str)  : Default challenge ID to write notes into.
                               Default: human-cli-notes
"""

from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_MEMORY_CORE_URL = "http://127.0.0.1:8777"
DEFAULT_CHALLENGE_ID = "human-cli-notes"


class NoteBridge:
    """Relays Human CLI notes and commands to Memory Core via ``/ingest``.

    Usage::

        bridge = NoteBridge()
        resp = bridge.ingest_note("found port 8080 open", cwd="/home/user")
        # resp == { "revision_before": 0, "revision_after": 1,
        #           "summary": "...", "diff": {...}, "source_id": "I-..." }

    The caller owns all write decisions after the ingest response returns.
    """

    def __init__(
        self,
        *,
        memory_core_url: str | None = None,
        challenge_id: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.base_url = (
            memory_core_url or os.environ.get("MEMORY_CORE_URL", DEFAULT_MEMORY_CORE_URL)
        ).rstrip("/")
        self.challenge_id = (
            challenge_id or os.environ.get("MEMORY_CORE_CHALLENGE", DEFAULT_CHALLENGE_ID)
        )
        self.timeout = timeout
        self._http = httpx.Client(timeout=timeout)

    # ── public API ──────────────────────────────────────────────────────────

    def ingest_note(
        self,
        note_text: str,
        *,
        cwd: str = "",
        actor: str = "human",
    ) -> dict[str, Any]:
        """Send a human note as a ``NoteInput`` ingest.

        Returns the ingest response dict with keys:
        ``revision_before``, ``revision_after``, ``summary``, ``diff``, ``source_id``.
        """
        return self._ingest(self._build_envelope({
            "type": "note",
            "content": note_text,
        }, actor=actor))

    def ingest_command(
        self,
        command: str,
        *,
        cwd: str = "",
        exit_code: int = 0,
        stdout: str = "",
        stderr: str = "",
        actor: str = "human",
    ) -> dict[str, Any]:
        """Send a terminal command as a ``CommandResultInput`` ingest.

        Returns the ingest response dict with keys:
        ``revision_before``, ``revision_after``, ``summary``, ``diff``, ``source_id``.
        """
        return self._ingest(self._build_envelope({
            "type": "command_result",
            "command": [command],
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
        }, actor=actor))

    def close(self) -> None:
        self._http.close()

    # ── query API ───────────────────────────────────────────────────────────

    def list_challenges(self) -> list[str]:
        """Return list of known challenge IDs from ``GET /v1/challenges``."""
        url = f"{self.base_url}/v1/challenges"
        resp = self._http.get(url)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Memory Core list_challenges failed (HTTP {resp.status_code}): {resp.text}"
            )
        return resp.json()

    def challenge_exists(self, challenge_id: str) -> bool:
        """Check if a challenge exists by reading its memory."""
        url = f"{self.base_url}/v1/challenges/{challenge_id}/memory"
        resp = self._http.get(url)
        return resp.status_code == 200

    def read_memory(
        self,
        challenge_id: str,
        *,
        layer: str | None = None,
        section: str | None = None,
    ) -> dict[str, Any]:
        """Read the whiteboard via ``GET /v1/challenges/{id}/memory``.

        Returns the MemoryReadResponse dict with keys:
        ``revision``, ``challenge``, ``fact``, ``assumption``, ``guessing``.
        """
        url = f"{self.base_url}/v1/challenges/{challenge_id}/memory"
        params: dict[str, str] = {}
        if layer:
            params["layer"] = layer
        if section:
            params["section"] = section
        resp = self._http.get(url, params=params)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Memory Core read_memory failed (HTTP {resp.status_code}): {resp.text}"
            )
        return resp.json()

    def guess(
        self,
        challenge_id: str,
        *,
        count: int = 3,
        only_untried: bool = True,
        seed: int | None = None,
    ) -> dict[str, Any]:
        """Draw guessing techniques via ``POST /v1/challenges/{id}/guess``.

        Returns the GuessResponse dict with keys:
        ``catalog``, ``revision``, ``candidates``.
        """
        url = f"{self.base_url}/v1/challenges/{challenge_id}/guess"
        body: dict[str, Any] = {"count": count, "only_untried": only_untried}
        if seed is not None:
            body["seed"] = seed
        resp = self._http.post(url, json=body)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Memory Core guess failed (HTTP {resp.status_code}): {resp.text}"
            )
        return resp.json()

    def query_nl(
        self,
        challenge_id: str,
        question: str,
        *,
        layers: list[str] | None = None,
    ) -> dict[str, Any]:
        """Natural-language query via ``POST /v1/challenges/{id}/query``.

        Returns the QueryResponse dict with keys:
        ``answer``, ``references``, ``revision``.
        """
        url = f"{self.base_url}/v1/challenges/{challenge_id}/query"
        body: dict[str, Any] = {"question": question}
        if layers:
            body["layers"] = layers
        resp = self._http.post(url, json=body)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Memory Core query failed (HTTP {resp.status_code}): {resp.text}"
            )
        return resp.json()

    # ── internals ───────────────────────────────────────────────────────────

    def _build_envelope(self, input_body: dict[str, Any], *, actor: str) -> dict[str, Any]:
        return {
            "source": {
                "type": "human",
                "actor": actor,
            },
            "input": input_body,
        }

    def _ingest(self, envelope: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/v1/challenges/{self.challenge_id}/ingest"

        resp = self._http.post(url, json=envelope)

        if resp.status_code == 404:
            # Challenge does not exist yet → auto-create it, then retry.
            self._ensure_challenge()
            resp = self._http.post(url, json=envelope)

        if resp.status_code >= 400:
            raise RuntimeError(
                f"Memory Core ingest failed (HTTP {resp.status_code}): {resp.text}"
            )

        return resp.json()

    def _ensure_challenge(self) -> None:
        """Auto-create the default challenge if it does not exist."""
        url = f"{self.base_url}/v1/challenges"
        body = {
            "id": self.challenge_id,
            "title": f"Human CLI Notes ({self.challenge_id})",
            "initial_input": None,
        }
        create_resp = self._http.post(url, json=body)
        # 409 = already exists (race with another client) — fine.
        if create_resp.status_code >= 400 and create_resp.status_code != 409:
            raise RuntimeError(
                f"Failed to create challenge {self.challenge_id}: "
                f"HTTP {create_resp.status_code}: {create_resp.text}"
            )
