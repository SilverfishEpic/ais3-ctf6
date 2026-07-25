"""Environment-driven configuration.

Secrets live only in the environment; nothing here is ever written to the repository.
See ``.env.example``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

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
        return cls(
            runs_dir=Path(os.environ.get("MEMORY_CORE_RUNS_DIR", "./runs")),
            templates_dir=Path(os.environ.get("MEMORY_CORE_TEMPLATES_DIR", "./templates")),
            ais3_base_url=os.environ.get("AIS3_BASE_URL", DEFAULT_AIS3_BASE_URL),
            ais3_api_key=os.environ.get("AIS3_API_KEY") or None,
            ais3_model=os.environ.get("AIS3_SUMMARIZER_MODEL", DEFAULT_AIS3_MODEL),
            ais3_timeout=_float_env("AIS3_TIMEOUT", DEFAULT_AIS3_TIMEOUT),
        )


def _float_env(name: str, default: float) -> float:
    """Read a numeric setting, falling back rather than crashing on a typo at boot."""

    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default
