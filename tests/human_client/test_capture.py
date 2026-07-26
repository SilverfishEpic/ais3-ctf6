from __future__ import annotations

import io
import sys

import pytest

from human_client.capture import run_capture


def test_capture_tees_stdout_and_stderr() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    result = run_capture(
        [
            sys.executable,
            "-c",
            "import sys; print('out'); print('err', file=sys.stderr)",
        ],
        stdout=stdout,
        stderr=stderr,
    )

    assert result.exit_code == 0
    assert result.stdout == "out\n"
    assert result.stderr == "err\n"
    assert stdout.getvalue() == "out\n"
    assert stderr.getvalue() == "err\n"


def test_capture_preserves_nonzero_exit_code() -> None:
    result = run_capture([sys.executable, "-c", "import sys; sys.exit(7)"])

    assert result.exit_code == 7
    assert result.command[:2] == [sys.executable, "-c"]


def test_capture_requires_structured_argv() -> None:
    with pytest.raises(ValueError):
        run_capture([])
