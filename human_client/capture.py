from __future__ import annotations

import subprocess
import sys
import threading
from base64 import b64encode
from dataclasses import dataclass
from typing import BinaryIO, TextIO


@dataclass(frozen=True)
class CaptureResult:
    command: list[str]
    exit_code: int
    stdout_bytes: bytes
    stderr_bytes: bytes

    @property
    def stdout(self) -> str:
        return self.stdout_bytes.decode("utf-8", errors="replace")

    @property
    def stderr(self) -> str:
        return self.stderr_bytes.decode("utf-8", errors="replace")

    @property
    def stdout_base64(self) -> str:
        return b64encode(self.stdout_bytes).decode("ascii")

    @property
    def stderr_base64(self) -> str:
        return b64encode(self.stderr_bytes).decode("ascii")

    def as_command_result(self) -> dict[str, object]:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "stdout_base64": self.stdout_base64,
            "stderr_base64": self.stderr_base64,
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
        bufsize=0,
    )
    captured_stdout = bytearray()
    captured_stderr = bytearray()

    def pump(source: BinaryIO, sink: TextIO, capture: bytearray) -> None:
        for chunk in iter(lambda: source.read(8192), b""):
            capture.extend(chunk)
            binary_sink = getattr(sink, "buffer", None)
            if binary_sink is not None:
                binary_sink.write(chunk)
                binary_sink.flush()
            else:
                sink.write(chunk.decode("utf-8", errors="replace"))
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
        stdout_bytes=bytes(captured_stdout),
        stderr_bytes=bytes(captured_stderr),
    )
