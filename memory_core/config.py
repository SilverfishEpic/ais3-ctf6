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


@dataclass(frozen=True)
class Settings:
    runs_dir: Path
    templates_dir: Path
    ais3_base_url: str
    ais3_api_key: str | None
    ais3_model: str

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            runs_dir=Path(os.environ.get("MEMORY_CORE_RUNS_DIR", "./runs")),
            templates_dir=Path(os.environ.get("MEMORY_CORE_TEMPLATES_DIR", "./templates")),
            ais3_base_url=os.environ.get("AIS3_BASE_URL", DEFAULT_AIS3_BASE_URL),
            ais3_api_key=os.environ.get("AIS3_API_KEY") or None,
            ais3_model=os.environ.get("AIS3_SUMMARIZER_MODEL", DEFAULT_AIS3_MODEL),
        )
