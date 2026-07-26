from __future__ import annotations

from pathlib import Path

HUMAN_CLIENT_ROOT = Path(__file__).resolve().parents[2] / "human_client"


def test_human_client_does_not_import_memory_core_or_read_local_state() -> None:
    text = "\n".join(path.read_text(encoding="utf-8") for path in HUMAN_CLIENT_ROOT.glob("*.py"))

    assert "memory_core" not in text
    assert "state.yaml" not in text
    assert "journal.jsonl" not in text
    assert "AIS3_API_KEY" not in text
    assert "AIS3_BASE_URL" not in text
    assert "AIS3_SUMMARIZER_MODEL" not in text


def test_human_client_does_not_use_shell_execution() -> None:
    text = "\n".join(path.read_text(encoding="utf-8") for path in HUMAN_CLIENT_ROOT.glob("*.py"))

    assert "shell=True" not in text
    assert "os.system" not in text
    assert "PROMPT_COMMAND" not in text
