#!/usr/bin/env python3
"""claude_loop 環境初始化腳本。

用法
----
    python -m init.setup                          # 檢查並回報狀態
    python -m init.setup --fix                    # 自動建立遺失的目錄與 .env
    python -m init.setup --install                # 同上 + pip install

做什麼
------
1. 檢查 Python 版本（需要 >= 3.11）
2. 建立必要的目錄（runs/ 等）
3. 從 .env.example 產生 .env（若尚不存在）
4. 檢查 templates/ 是否有 generic.yaml 及其他模板
5. 安裝 pyproject.toml 定義的依賴（--install）
6. 回報環境狀態
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

# ── 專案內的路徑 ──────────────────────────────────────────────────

HERE = Path(__file__).resolve().parent           # init/
PROJECT_ROOT = HERE.parent                        # anti-complexity/
CORE = PROJECT_ROOT / "core"                      # core/
CLAUDE_LOOP = CORE / "claude_loop"                # core/claude_loop/

REQUIRED_DIRS: list[Path] = [
    CLAUDE_LOOP / "runs",
]

REQUIRED_TEMPLATES = ("generic.yaml",)
OPTIONAL_TEMPLATES = ("web.yaml", "rev.yaml", "pwn.yaml", "crypto.yaml")

ENV_EXAMPLE = HERE / ".env.example"
ENV_TARGET = CLAUDE_LOOP / ".env"


# ── 檢查函式 ─────────────────────────────────────────────────────


def _ok(text: str) -> None:
    print(f"  [OK]  {text}")


def _warn(text: str) -> None:
    print(f"  [..]  {text}")


def _fail(text: str) -> None:
    print(f"  [!!]  {text}")


def check_python() -> bool:
    ok = sys.version_info >= (3, 11)
    if ok:
        _ok(f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    else:
        _fail(f"Python {sys.version_info.major}.{sys.version_info.minor}（需要 >= 3.11）")
    return ok


def check_dirs() -> dict[Path, bool]:
    results: dict[Path, bool] = {}
    for d in REQUIRED_DIRS:
        exists = d.is_dir()
        results[d] = exists
        if exists:
            _ok(f"目錄 {d.relative_to(PROJECT_ROOT)}")
        else:
            _fail(f"目錄 {d.relative_to(PROJECT_ROOT)} 不存在")
    return results


def check_templates() -> tuple[list[str], list[str]]:
    templates_dir = CLAUDE_LOOP / "templates"
    if not templates_dir.is_dir():
        _fail(f"模板目錄 {templates_dir.relative_to(PROJECT_ROOT)} 不存在")
        return [], []

    found = [p.stem for p in sorted(templates_dir.glob("*.yaml"))]
    missing_required = [t for t in REQUIRED_TEMPLATES if t.removesuffix(".yaml") not in found]
    missing_optional = [t for t in OPTIONAL_TEMPLATES if t.removesuffix(".yaml") not in found]

    if not missing_required:
        _ok(f"必要模板都存在（{', '.join(found)}）")
    else:
        for t in missing_required:
            _fail(f"必要模板 {t} 找不到")

    if missing_optional:
        for t in missing_optional:
            _warn(f"選擇性模板 {t} 找不到（不影響啟動）")

    return missing_required, missing_optional


def check_env() -> bool:
    if ENV_TARGET.is_file():
        _ok(f".env 已存在（{ENV_TARGET.relative_to(PROJECT_ROOT)}）")
        return True
    else:
        _fail(f".env 不存在（預期路徑 {ENV_TARGET.relative_to(PROJECT_ROOT)}）")
        return False


def check_env_values() -> list[str]:
    """檢查 .env 裡有沒有該填但是空的變數。"""
    if not ENV_TARGET.is_file():
        return []
    missing: list[str] = []
    required_keys = ["AIS3_API_KEY"]
    content = ENV_TARGET.read_text(encoding="utf-8")
    for key in required_keys:
        val = _parse_env_value(content, key)
        if not val:
            missing.append(key)
            _warn(f"{key} 在 .env 中未設定")
    return missing


def _parse_env_value(content: str, key: str) -> str | None:
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() == key:
            return v.strip().strip("\"'") or None
    return None


# ── 自動修復 ────────────────────────────────────────────────────


def create_dirs() -> None:
    for d in REQUIRED_DIRS:
        d.mkdir(parents=True, exist_ok=True)
        _ok(f"建立目錄 {d.relative_to(PROJECT_ROOT)}")


def create_env() -> None:
    if ENV_TARGET.is_file():
        _ok(f".env 已存在，略過")
        return
    if not ENV_EXAMPLE.is_file():
        _fail(f".env.example 不在預期位置（{ENV_EXAMPLE}）")
        return
    shutil.copy2(str(ENV_EXAMPLE), str(ENV_TARGET))
    _ok(f"從 .env.example 複製 .env 到 {ENV_TARGET.relative_to(PROJECT_ROOT)}")
    _warn("請編輯 .env 填入 AIS3_API_KEY 等設定")


def install_deps() -> None:
    pip = Path(shutil.which("pip") or shutil.which("pip3") or "")
    if not pip:
        _fail("找不到 pip，跳過安裝")
        return

    pyproject = PROJECT_ROOT / "pyproject.toml"
    if not pyproject.is_file():
        _fail(f"找不到 {pyproject.relative_to(PROJECT_ROOT)}，跳過安裝")
        return

    _ok(f"使用 {pip} 安裝依賴…")
    result = subprocess.run(
        [str(pip), "install", "-e", str(PROJECT_ROOT)],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        _ok("依賴安裝完成")
    else:
        _warn(f"pip install 回傳 {result.returncode}")
        for line in result.stderr.strip().splitlines():
            print(f"       {line}")


# ── 摘要 ─────────────────────────────────────────────────────────


def print_summary(env_missing_keys: list[str]) -> None:
    print()
    print("── 啟動檢查摘要 ──")

    if env_missing_keys:
        print("  [..]  .env 已存在但以下變數尚未設定：")
        for k in env_missing_keys:
            print(f"        - {k}")
        print("  編輯 .env 填入值後即可啟動 Memory Core。")
    else:
        print("  [OK]  環境檢查通過，可以啟動。")
    print()

    print("  Memory Core 啟動指令：")
    print(f"    cd {PROJECT_ROOT}")
    print("    python -m core.claude_loop.memory.core")
    print()
    print("  單輪 normal 模式（範例，需自行撰寫膠水）：")
    print("    from core.claude_loop.bridge.loop import run_normal")
    print("    from core.claude_loop.adapters.claude import ClaudeAdapter")
    print("    from core.claude_loop.memory.client.http import HttpMemoryClient")
    print()


# ── 主程式 ──────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="claude_loop_setup", description=__doc__)
    parser.add_argument("--fix", action="store_true", help="自動建立遺失的目錄與 .env")
    parser.add_argument(
        "--install", action="store_true", help="等同 --fix 加上 pip install -e"
    )
    args = parser.parse_args(argv)

    do_fix = args.fix or args.install
    do_install = args.install

    print(f"claude_loop 環境初始化  —  {platform.node()}")
    print(f"工作目錄: {CLAUDE_LOOP}")
    print()

    # ── 檢查 ──
    py_ok = check_python()
    dirs = check_dirs()
    check_templates()
    env_ok = check_env()
    env_missing = check_env_values() if env_ok else ["AIS3_API_KEY"]

    if not py_ok:
        print("Python 版本不足，無法繼續。")
        return 1

    # ── 自動修復 ──
    if do_fix:
        print()
        print("── 自動修復 ──")
        create_dirs()
        if not env_ok:
            create_env()
        else:
            _ok(f".env 已存在，略過")

    if do_install:
        print()
        install_deps()

    # ── 摘要 ──
    print()
    print_summary(env_missing)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
