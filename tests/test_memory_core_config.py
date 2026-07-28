from __future__ import annotations

import json
from pathlib import Path

from memory_core.config import Settings


def test_core_config_file_supplies_non_secret_model_defaults(
    tmp_path: Path, monkeypatch: object
) -> None:
    path = tmp_path / "core-config.json"
    path.write_text(
        json.dumps(
            {
                "ais3_base_url": "https://model.invalid/v1",
                "ais3_model": "test-model",
                "ais3_timeout": 42,
                "runs_dir": "runtime",
                "templates_dir": "template-set",
                "ais3_api_key": "must-not-be-read",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AIS3_API_KEY", "process-secret")  # type: ignore[union-attr]

    settings = Settings.from_sources(path)

    assert settings.ais3_base_url == "https://model.invalid/v1"
    assert settings.ais3_model == "test-model"
    assert settings.ais3_timeout == 42
    assert settings.runs_dir == Path("runtime")
    assert settings.templates_dir == Path("template-set")
    assert settings.ais3_api_key == "process-secret"


def test_core_model_environment_is_not_a_configuration_source(
    tmp_path: Path, monkeypatch: object
) -> None:
    monkeypatch.setenv("AIS3_SUMMARIZER_MODEL", "environment-model")  # type: ignore[union-attr]

    settings = Settings.from_sources(tmp_path / "missing.json")

    assert settings.ais3_model != "environment-model"
