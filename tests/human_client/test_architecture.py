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


BANNED_SUBSTRINGS = (
    "memory_core",
    "state.yaml",
    "journal.jsonl",
    "AIS3_API_KEY",
    "AIS3_BASE_URL",
    "AIS3_SUMMARIZER_MODEL",
    "shell=True",
    "os.system",
    "PROMPT_COMMAND",
)


def test_every_human_client_module_including_subpackages_is_clean() -> None:
    """Hold subpackages to the same nine bans the two tests above apply to the top level.

    Both of those tests glob ``*.py``, which does not descend, so every module in a
    subpackage such as ``human_client/web/`` escapes all nine. Without this test the
    boundary would hold for the files that predate the subpackage and for nothing written
    afterwards.

    Failures name the file and the substring, which a membership assertion over one
    concatenated blob cannot do.
    """

    offences = [
        f"{path.relative_to(HUMAN_CLIENT_ROOT)}: {banned}"
        for path in sorted(HUMAN_CLIENT_ROOT.rglob("*.py"))
        for banned in BANNED_SUBSTRINGS
        if banned in path.read_text(encoding="utf-8")
    ]

    assert offences == []
