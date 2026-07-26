#!/usr/bin/env python3
from pathlib import Path

from core.human_cli.shell import PowerShellNoteApp


def main() -> int:
    script_dir = Path(__file__).resolve().parent
    app = PowerShellNoteApp(script_dir=script_dir)
    return app.run()


if __name__ == "__main__":
    raise SystemExit(main())
