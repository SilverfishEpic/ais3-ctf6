from __future__ import annotations

import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import TextIO


@dataclass(frozen=True)
class CaptureResult:
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str

    def as_command_result(self) -> dict[str, object]:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


def run_capture(
    argv: list[str], *, stdout: TextIO | None = None, stderr: TextIO | None = None
) -> CaptureResult:
    if not argv:
        raise ValueError("capture requires a command after --")
    out_stream = stdout or sys.stdout
    err_stream = stderr or sys.stderr
    process = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    captured_stdout: list[str] = []
    captured_stderr: list[str] = []

    def pump(source: TextIO, sink: TextIO, capture: list[str]) -> None:
        for chunk in iter(source.readline, ""):
            capture.append(chunk)
            sink.write(chunk)
            sink.flush()

    assert process.stdout is not None
    assert process.stderr is not None
    out_thread = threading.Thread(target=pump, args=(process.stdout, out_stream, captured_stdout))
    err_thread = threading.Thread(target=pump, args=(process.stderr, err_stream, captured_stderr))
    out_thread.start()
    err_thread.start()
    exit_code = process.wait()
    out_thread.join()
    err_thread.join()
    return CaptureResult(
        command=list(argv),
        exit_code=exit_code,
        stdout="".join(captured_stdout),
        stderr="".join(captured_stderr),
    )
