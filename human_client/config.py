from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_API_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT = 30.0
DEFAULT_CONFIG_PATH = Path(".ctfmem/client-config.json")


@dataclass(frozen=True)
class ClientConfig:
    api_url: str
    challenge: str | None
    actor: str
    timeout: float
    json_output: bool
    verbose: bool
    color: bool
    # Kept with the other project-local defaults so the web launcher and the CLI use the
    # same explicitly approved directory.  It is not a credential and is intentionally
    # optional: agent support stays disabled until the operator names one.
    agent_workspace: str | None = None


@dataclass(frozen=True)
class SavedClientConfig:
    """Non-secret, project-local defaults for the human CLI."""

    api_url: str | None = None
    challenge: str | None = None
    actor: str | None = None
    timeout: float | None = None
    agent_workspace: str | None = None

    def to_dict(self) -> dict[str, object]:
        values = {
            "api_url": self.api_url,
            "challenge": self.challenge,
            "actor": self.actor,
            "timeout": self.timeout,
            "agent_workspace": self.agent_workspace,
        }
        return {key: value for key, value in values.items() if value is not None}


def config_from_args(args: object) -> ClientConfig:
    saved = load_saved_client_config(config_path_from_args(args))
    api_url = _get(args, "api_url") or saved.api_url or DEFAULT_API_URL
    challenge = _get(args, "challenge") or saved.challenge or None
    actor = _get(args, "actor") or saved.actor or "human"
    timeout_raw = _get(args, "timeout") or saved.timeout or DEFAULT_TIMEOUT
    timeout = float(str(timeout_raw))
    return ClientConfig(
        api_url=str(api_url),
        challenge=str(challenge) if challenge else None,
        actor=str(actor),
        timeout=timeout,
        json_output=bool(_get(args, "json")),
        verbose=bool(_get(args, "verbose")),
        color=not bool(_get(args, "no_color")),
        agent_workspace=saved.agent_workspace,
    )


def require_challenge(config: ClientConfig, override: str | None = None) -> str:
    challenge = override or config.challenge
    if not challenge:
        raise ValueError("challenge id is required; pass --challenge or run ctfmem config set")
    return challenge


def _get(args: object, name: str) -> object | None:
    return getattr(args, name, None)


def config_path_from_args(args: object) -> Path:
    raw_path = _get(args, "config")
    return Path(str(raw_path)) if raw_path else DEFAULT_CONFIG_PATH


def load_saved_client_config(path: Path = DEFAULT_CONFIG_PATH) -> SavedClientConfig:
    """Return safe defaults when local configuration is absent or malformed."""

    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return SavedClientConfig()
    if not isinstance(raw, dict):
        return SavedClientConfig()
    return SavedClientConfig(
        api_url=_optional_string(raw.get("api_url")),
        challenge=_optional_string(raw.get("challenge")),
        actor=_optional_string(raw.get("actor")),
        timeout=_optional_positive_float(raw.get("timeout")),
        agent_workspace=_optional_string(raw.get("agent_workspace")),
    )


def save_client_config(path: Path, config: SavedClientConfig) -> None:
    """Persist only public client defaults; credentials are intentionally never accepted."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config.to_dict(), indent=2) + "\n", encoding="utf-8")


def merge_saved_client_config(
    current: SavedClientConfig,
    *,
    api_url: str | None = None,
    challenge: str | None = None,
    actor: str | None = None,
    timeout: float | None = None,
    agent_workspace: str | None = None,
) -> SavedClientConfig:
    return SavedClientConfig(
        api_url=api_url if api_url is not None else current.api_url,
        challenge=challenge if challenge is not None else current.challenge,
        actor=actor if actor is not None else current.actor,
        timeout=timeout if timeout is not None else current.timeout,
        agent_workspace=(
            agent_workspace if agent_workspace is not None else current.agent_workspace
        ),
    )


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_positive_float(value: object) -> float | None:
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None
