"""The smoke-test client's rendering, which is the only logic it owns.

`scripts/memctl.py` is not the Human Client — Person 2 owns that — but it is checked in,
so its pure functions get the same treatment as everything else. What is worth pinning
down is that it renders the *contract* correctly: a promotion must not look like an
addition plus a removal, and a no-op must not look like a change.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import memctl  # noqa: E402


def _envelope(before: int = 1, after: int = 2, **diff: object) -> dict[str, object]:
    base = {
        "fact_added": [],
        "fact_updated": [],
        "fact_removed": [],
        "assumption_added": [],
        "assumption_updated": [],
        "assumption_removed": [],
        "promoted": [],
        "demoted": [],
        "template_changed": None,
    }
    base.update(diff)
    return {"revision_before": before, "revision_after": after, "summary": None, "diff": base}


def test_noop_ingest_is_not_rendered_as_a_change() -> None:
    """revision_before == revision_after means nothing was worth recording."""

    rendered = memctl.render_diff(_envelope(before=4, after=4))
    assert "unchanged" in rendered
    assert "->" not in rendered


def test_additions_render() -> None:
    rendered = memctl.render_diff(_envelope(fact_added=["F001"], assumption_added=["A001"]))
    assert "rev 1 -> 2" in rendered
    assert "+ FACT: F001" in rendered
    assert "+ ASSUMPTION: A001" in rendered


def test_promotion_renders_once_with_both_ids() -> None:
    """The old id under PROMOTED, the new one as the fact that now exists — and no
    spurious removal, because the claim did not go away."""

    rendered = memctl.render_diff(_envelope(promoted=["A001"], fact_added=["F002"]))
    assert "> PROMOTED: A001" in rendered
    assert "+ FACT: F002" in rendered
    assert "ASSUMPTION" not in rendered


def test_template_change_renders_the_from_alias() -> None:
    rendered = memctl.render_diff(_envelope(template_changed={"from": "generic@1", "to": "web@1"}))
    assert "TEMPLATE: generic@1 -> web@1" in rendered


def test_memory_render_shows_ids_evidence_and_promotion_ancestry() -> None:
    board = {
        "revision": 3,
        "challenge": {
            "id": "demo-web",
            "title": "Demo Web",
            "template": "web",
            "template_version": 1,
            "summary": "two services confirmed",
        },
        "fact": {
            "target": [
                {"id": "F001", "text": "TCP/80 is open.", "evidence": ["I-abc"], "tags": []}
            ],
            "general": [
                {
                    "id": "F002",
                    "text": "HTTP app confirmed.",
                    "evidence": ["I-abc", "M-def"],
                    "tags": [],
                    "promoted_from": "A001",
                }
            ],
            "progress": [],
        },
        "assumption": {"general": []},
        "guessing": {"catalog": "web@1", "state": {"WEB-SQLI": {"tried": True}}},
    }
    rendered = memctl.render_memory(board)

    assert "Demo Web (demo-web)  rev 3  template web@1" in rendered
    assert "F001  (target) TCP/80 is open." in rendered
    assert "<- A001" in rendered  # promotion ancestry is visible
    assert "evidence: I-abc, M-def" in rendered
    assert "GUESSING  web@1  (1 tried)" in rendered
    assert "ASSUMPTION" not in rendered  # empty layer is omitted, not printed blank


def test_every_documented_error_code_has_a_hint() -> None:
    """A code without advice is a code the user has to go read the spec for."""

    assert set(memctl.HINTS) >= {
        "summarizer_unauthorized",
        "summarizer_unavailable",
        "revision_conflict",
        "challenge_not_found",
    }
    # The distinction that matters: one is retryable, the other never is.
    assert "not help" in memctl.HINTS["summarizer_unauthorized"]
    assert "retry" in memctl.HINTS["summarizer_unavailable"]


def test_api_error_survives_a_body_it_does_not_understand() -> None:
    error = memctl.ApiError(500, {"unexpected": "shape"})
    assert error.code == "http_500"
    assert error.details == {}


@pytest.mark.parametrize(
    "argv, expected",
    [
        (["ls"], "ls"),
        (["show", "demo"], "show"),
        (["promote", "demo", "A001", "--reason", "verified"], "promote"),
    ],
)
def test_parser_accepts_the_documented_invocations(argv: list[str], expected: str) -> None:
    args = memctl.build_parser().parse_args(argv)
    assert args.subcommand == expected


def test_mutations_require_a_reason() -> None:
    """Mandatory at the API; the client refuses before spending a round trip."""

    with pytest.raises(SystemExit):
        memctl.build_parser().parse_args(["promote", "demo", "A001"])


def test_capture_keeps_the_commands_own_flags() -> None:
    """`--` protects the captured command's flags from being read as memctl's own.

    argparse consumes the separator itself, so `-sV` reaches the subprocess intact.
    """

    args = memctl.build_parser().parse_args(["capture", "demo", "--", "nmap", "-sV", "target"])
    assert args.command == ["nmap", "-sV", "target"]


def test_capture_tolerates_a_second_separator() -> None:
    """Belt and braces: argparse strips one `--`, cmd_capture strips a leftover."""

    args = memctl.build_parser().parse_args(["capture", "demo", "--", "--", "ls", "-l"])
    assert args.command == ["--", "ls", "-l"]
    assert (args.command[1:] if args.command[:1] == ["--"] else args.command) == ["ls", "-l"]
