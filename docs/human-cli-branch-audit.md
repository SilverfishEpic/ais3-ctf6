# human-cli branch 檢查與修復報告

## 1. 檢查基準

- Main baseline: `2c596a457842beb9a4688c22dc3de484180388f4`
- 原始 human-cli head: `e7757798e800e35734aabccac3db6a6165e8c617`

## 2. 原始分支問題

原始 `origin/human-cli` 不符合 Human CLI 規格，主要問題：

- 使用 `core/human_cli`，套件名稱與 Memory Core 容易混淆。
- 包含 `core/codex_loop/memory_core` stale Core copy。
- 包含 `core/spec` stale schema/spec copy。
- 包含 `core/note_ai` direct LLM note subsystem。
- 包含 `AI_API_KEY` / `AI_BASE_URL` / `AI_MODEL` 相關路徑。
- `core/human_cli/shell.py` 直接讀 `runs/<challenge>/state.yaml`。
- 使用 `subprocess.run(..., shell=True)`。
- 包含 shell profile recorder 與 `rounds.jsonl` workflow。
- 包含 `__pycache__` runtime artifacts。
- pytest collection、ruff、mypy 均失敗。

## 3. 修復內容

- 移除違規 `core/` 舊實作與 stale copies。
- 移除 direct LLM note subsystem。
- 移除 shell setup / init scripts 與 runtime artifacts。
- 新增正式 `human_client/` package。
- 新增 `ctfmem = human_client.cli:main` console entry point。
- 新增 centralized HTTP client：`human_client/client.py`。
- 新增 tee-like capture：`human_client/capture.py`。
- 新增 renderer / error mapping / config。
- 新增 `docs/human-cli.md` 作為正式規格與驗收文件。
- 新增 Human CLI tests under `tests/human_client/`。

## 4. 合規狀態

- 只透過 Memory Core HTTP API 操作。
- 不直接讀寫 `state.yaml` / `journal.jsonl`。
- 不直接呼叫 AIS3。
- 不使用 shell profile injection。
- 不使用 `shell=True` / `os.system()`。
- Mutation 需要 explicit revision / actor / reason。
- Revision conflict 不自動 retry。
- JSON mode 輸出 machine-safe JSON。

## 5. 尚未 merge

本修復僅在 `human-cli` 分支完成，不合併進 `main`。
