from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_API_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT = 30.0


@dataclass(frozen=True)
class ClientConfig:
    api_url: str
    challenge: str | None
    actor: str
    timeout: float
    json_output: bool
    verbose: bool
    color: bool


def config_from_args(args: object) -> ClientConfig:
    api_url = _get(args, "api_url") or os.getenv("CTFMEM_API_URL") or DEFAULT_API_URL
    challenge = _get(args, "challenge") or os.getenv("CTFMEM_CHALLENGE") or None
    actor = _get(args, "actor") or os.getenv("CTFMEM_ACTOR") or os.getenv("USER") or "human"
    timeout_raw = _get(args, "timeout") or os.getenv("CTFMEM_TIMEOUT") or DEFAULT_TIMEOUT
    timeout = float(str(timeout_raw))
    return ClientConfig(
        api_url=str(api_url),
        challenge=str(challenge) if challenge else None,
        actor=str(actor),
        timeout=timeout,
        json_output=bool(_get(args, "json")),
        verbose=bool(_get(args, "verbose")),
        color=not bool(_get(args, "no_color")),
    )


def require_challenge(config: ClientConfig, override: str | None = None) -> str:
    challenge = override or config.challenge
    if not challenge:
        raise ValueError("challenge id is required; pass --challenge or set CTFMEM_CHALLENGE")
    return challenge


def _get(args: object, name: str) -> object | None:
    return getattr(args, name, None)
