"""AIS3 summarizer backend (OpenAI-compatible Chat Completions).

Source: https://github.com/chunying/ais3-ai-infra

    AIS3_BASE_URL=https://llm-api.zoolab.org/v1
    AIS3_API_KEY=...
    AIS3_SUMMARIZER_MODEL=ais3/nemotron-3-ultra-550b

We rely only on the baseline OpenAI-compatible ``POST /chat/completions`` surface and do
**not** assume any provider-specific structured-output extension exists.  The contract is
therefore: ask for JSON in the prompt, parse it, validate it against the typed model, and
allow one bounded repair round-trip that feeds the validation error back.  If that fails,
raise rather than commit something unvalidated.

No test in the default suite touches this class over the network; see
``tests/test_ais3_live.py`` for the opt-in check.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import httpx

from ..errors import ProposalInvalid, SummarizerUnavailable
from ..models import (
    AnswerProposal,
    ClassificationProposal,
    MemorySnapshot,
    SourceRecord,
    SummaryProposal,
    TemplateDefinition,
)
from ..summarizer import extract_json, parse_proposal

EPISTEMIC_RULES = """\
You maintain a shared CTF whiteboard. You are NOT an agent: you never choose exploits,
never run commands, and never decide what to do next. You only organise what is known.

There are exactly two layers you may write to:
  FACT       - atomic, specific to this challenge, confirmed by DIRECT EVIDENCE.
  ASSUMPTION - atomic, specific, plausible and testable, but not yet verified.

Rules you must obey:
  1. Every fact you propose must cite at least one evidence source id from the input.
  2. Direct evidence means a tool/command result, or a first-hand human observation
     ("I ran nmap and port 80 is open"). It does NOT mean an inference, yours or anyone
     else's. An agent saying "this is probably SQL injection" is never evidence.
  3. A human's reasoning or guess is an ASSUMPTION, even when it sounds convincing.
     A human's report of something they directly observed may be a FACT.
  4. If the evidence is insufficient, write an ASSUMPTION or write nothing. Do not fill
     the whiteboard for the sake of filling it.
  5. You may promote an assumption to a fact ONLY when this input provides NEW direct
     evidence for it. Otherwise leave it alone; a human can override it themselves.
  6. You may not delete a fact. If you now believe one is wrong, demote it with a reason.
  7. You may not invent, modify or mention the guessing technique catalog. It belongs to
     the template.
  8. Never invent item ids. To modify an existing item, reference its exact existing id.
     New items get their ids assigned by the system, not by you.

Reply with a single JSON object and nothing else.
"""

PROPOSAL_SCHEMA_HINT = """\
{
  "challenge_summary": "string or null - a few sentences describing the challenge state",
  "fact_add":        [{"section": "...", "text": "...", "evidence": ["I-..."], "tags": []}],
  "fact_update":     [{"id": "F001", "text": "...", "section": "...", "evidence_add": ["I-..."]}],
  "assumption_add":  [{"section": "...", "text": "...", "evidence": ["I-..."], "tags": []}],
  "assumption_update": [{"id": "A001", "text": "...", "section": "...", "evidence_add": []}],
  "assumption_remove": ["A002"],
  "promote":         [{"id": "A003", "evidence": ["I-..."], "section": "..."}],
  "demote":          [{"id": "F004", "reason": "...", "section": "..."}],
  "note":            "one short sentence describing what changed"
}
Every key is optional; omit what does not apply. An empty object is a valid no-op.
"""


class AIS3Summarizer:
    """Chat-Completions-backed :class:`~memory_core.summarizer.Summarizer`."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        timeout: float = 120.0,
        max_repairs: int = 1,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_repairs = max_repairs
        self._client = client

    # -- Summarizer protocol ----------------------------------------------

    def summarize(
        self,
        *,
        state: MemorySnapshot,
        source_record: SourceRecord,
        template: TemplateDefinition,
    ) -> SummaryProposal:
        user = "\n\n".join(
            [
                f"## Current whiteboard (revision {state.revision})",
                _render_state(state),
                "## Template layout you must use",
                json.dumps(
                    {
                        "fact_sections": template.fact_sections,
                        "assumption_sections": template.assumption_sections,
                    },
                    indent=2,
                ),
                "## New input",
                json.dumps(
                    {
                        "evidence_id": source_record.id,
                        "actor": source_record.actor,
                        "source": source_record.source.model_dump(mode="json")
                        if source_record.source
                        else None,
                        "input_type": source_record.input_type,
                        "content": source_record.content,
                    },
                    indent=2,
                    ensure_ascii=False,
                )[:20000],
                "## Expected JSON shape",
                PROPOSAL_SCHEMA_HINT,
            ]
        )
        messages = [
            {"role": "system", "content": EPISTEMIC_RULES},
            {"role": "user", "content": user},
        ]
        return self._parse_with_repair(messages)

    def classify(
        self,
        *,
        initial_input: str,
        templates: Sequence[TemplateDefinition],
    ) -> ClassificationProposal:
        options = [
            {"id": t.id, "description": t.description, "hints": t.classifier_hints}
            for t in templates
        ]
        messages = [
            {
                "role": "system",
                "content": (
                    "Pick the single best CTF category template for a challenge. "
                    "If nothing clearly fits, pick 'generic'. Reply with JSON only: "
                    '{"template": "...", "confidence": "low|medium|high", "rationale": "..."}'
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Available templates:\n{json.dumps(options, indent=2)}\n\n"
                    f"Challenge input:\n{initial_input[:8000]}"
                ),
            },
        ]
        raw = self._chat(messages)
        try:
            return ClassificationProposal.model_validate(json.loads(extract_json(raw)))
        except Exception:
            # Classification is advisory; a bad answer must never block challenge creation.
            return ClassificationProposal(
                template="generic", confidence="low", rationale="fallback"
            )

    def answer(
        self,
        *,
        state: MemorySnapshot,
        question: str,
        layers: Sequence[str],
    ) -> AnswerProposal:
        messages = [
            {
                "role": "system",
                "content": (
                    "Answer strictly and only from the whiteboard below. Never use outside "
                    "knowledge, never speculate, and never invent item ids. If the whiteboard "
                    "does not contain the answer, say so plainly. Cite every item you used. "
                    'Reply with JSON only: {"answer": "...", "references": ["F001", "A002"]}'
                ),
            },
            {
                "role": "user",
                "content": (
                    f"## Whiteboard (revision {state.revision})\n"
                    f"{_render_state(state, layers=layers)}\n\n## Question\n{question}"
                ),
            },
        ]
        raw = self._chat(messages)
        try:
            return AnswerProposal.model_validate(json.loads(extract_json(raw)))
        except Exception:
            return AnswerProposal(answer=raw.strip()[:2000], references=[])

    # -- transport ---------------------------------------------------------

    def _parse_with_repair(self, messages: list[dict[str, str]]) -> SummaryProposal:
        conversation = list(messages)
        last_error: Exception | None = None
        for attempt in range(self.max_repairs + 1):
            raw = self._chat(conversation)
            try:
                return parse_proposal(raw)
            except ProposalInvalid as exc:
                last_error = exc
                if attempt >= self.max_repairs:
                    break
                conversation = conversation + [
                    {"role": "assistant", "content": raw[:4000]},
                    {
                        "role": "user",
                        "content": (
                            "That was not valid. Error: "
                            f"{json.dumps(exc.details, ensure_ascii=False)[:1500]}\n"
                            "Reply again with a single valid JSON object matching the shape "
                            "given earlier, and nothing else."
                        ),
                    },
                ]
        assert last_error is not None
        raise last_error

    def _chat(self, messages: list[dict[str, str]]) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
        }
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        client = self._client or httpx.Client(timeout=self.timeout)
        try:
            response = client.post(
                f"{self.base_url}/chat/completions", json=payload, headers=headers
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise SummarizerUnavailable(f"AIS3 request failed: {exc}") from exc
        finally:
            if self._client is None:
                client.close()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise SummarizerUnavailable("AIS3 response had no message content") from exc
        return str(content)


def _render_state(state: MemorySnapshot, layers: Sequence[str] = ("fact", "assumption")) -> str:
    """Compact, id-first rendering.  Ids must be reproduced exactly, so they lead."""

    lines: list[str] = [f"challenge: {state.challenge.title} ({state.challenge.id})"]
    if state.challenge.summary:
        lines.append(f"summary: {state.challenge.summary}")
    if "fact" in layers:
        lines.append("FACT:")
        for section, item in state.iter_facts():
            lines.append(f"  [{item.id}] ({section}) {item.text}")
    if "assumption" in layers:
        lines.append("ASSUMPTION:")
        for section, assumption in state.iter_assumptions():
            lines.append(f"  [{assumption.id}] ({section}) {assumption.text}")
    return "\n".join(lines)
