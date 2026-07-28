"""Memory Core configuration.

Non-secret service defaults may live in ``.ctfmem/core-config.json``. Secrets stay only
in the environment and are never read from a configuration file. See ``.env.example``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_AIS3_BASE_URL = "https://llm-api.zoolab.org/v1"
DEFAULT_AIS3_MODEL = "ais3/nemotron-3-ultra-550b"

#: Read timeout for one Summarizer call, in seconds.
#:
#: Measured against the real backend, a single summarize ranged from ~14s to ~57s for the
#: same prompt, so latency is dominated by contention rather than by our input size.  A
#: generous default is cheap here precisely because ingest holds **no lock** during the
#: model call: a slow request delays that one ingest and nothing else, while a too-tight
#: timeout throws away work that was about to succeed.
DEFAULT_AIS3_TIMEOUT = 300.0

#: Connect separately and much tighter — an unreachable host should fail in seconds, not
#: sit in the read budget.
CONNECT_TIMEOUT = 10.0
DEFAULT_CONFIG_PATH = Path(".ctfmem/core-config.json")


@dataclass(frozen=True)
class Settings:
    runs_dir: Path
    templates_dir: Path
    ais3_base_url: str
    ais3_api_key: str | None
    ais3_model: str
    ais3_timeout: float

    @classmethod
    def from_env(cls) -> Settings:
        return cls.from_sources()

    @classmethod
    def from_sources(cls, config_path: Path = DEFAULT_CONFIG_PATH) -> Settings:
        """Load public defaults from a local configuration file.

        ``AIS3_API_KEY`` is intentionally not a configuration-file field. It is always
        process-local environment state so a checked-in or copied project config cannot leak it.
        """

        saved = _load_config(config_path)
        return cls(
            runs_dir=Path(
                _string_setting(saved, "runs_dir") or "./runs"
            ),
            templates_dir=Path(
                _string_setting(saved, "templates_dir") or "./templates"
            ),
            ais3_base_url=(
                _string_setting(saved, "ais3_base_url") or DEFAULT_AIS3_BASE_URL
            ),
            ais3_api_key=os.environ.get("AIS3_API_KEY") or None,
            ais3_model=_string_setting(saved, "ais3_model") or DEFAULT_AIS3_MODEL,
            ais3_timeout=_positive_float(saved.get("ais3_timeout")) or DEFAULT_AIS3_TIMEOUT,
        )


def _load_config(path: Path) -> dict[str, Any]:
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _string_setting(settings: dict[str, Any], name: str) -> str | None:
    value = settings.get(name)
    return value if isinstance(value, str) and value else None


def _positive_float(value: object) -> float | None:
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None
