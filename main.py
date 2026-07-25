#!/usr/bin/env python3
import sys
from pathlib import Path

from core.human_cli.shell import NoteApp


def main() -> int:
    # 取得 main.py 所在的絕對路徑目錄
    script_dir = Path(__file__).resolve().parent

    # 實例化 App（這時候才會帶進 shell.py 裡的 self）
    app = NoteApp(script_dir=script_dir)

    # 啟動程式
    return app.run()


if __name__ == "__main__":
    raise SystemExit(main())