# Repository 殘留盤點與功能落差報告

日期：2026-07-26  
基準分支：`feature/ctfmem-agent-cli`

## 1. 結論

- `fixtures/claude/*.jsonl` 不是執行殘留；它們是 Agent Bridge 文件指定的 sanitized replay fixtures，用於離線、無 Claude 憑證、無網路的 deterministic 測試。
- `tests/snapshots/*.json` 是測試 snapshot，不是 runtime residue。
- 已清除本機執行殘留：Python bytecode/cache、pytest/mypy/ruff cache、舊的 ignored `core/` pycache 目錄。
- 已補強 `.gitignore`，避免 coverage、type-check daemon、Hypothesis、runtime status、stop sentinel、log/pid/tmp 等檔案被誤加入。
- 未發現 tracked fixture/snapshot 含本機絕對路徑、Authorization bearer、或 AIS3/OpenAI/Claude 類 secret literal。

## 2. 保留的 fixture 與理由

保留：

```text
fixtures/claude/
tests/snapshots/
```

理由：

- `docs/agent-bridge.md` 明確要求用 `fixtures/claude/*.jsonl` 驗證 `talk_only`、`single_tool`、`multi_tool`、`tool_failure`、`sandbox_denied`、`interrupted` 等情境。
- `docs/agent-bridge/real-sdk-findings.md` 說明這些 fixture 來自真實 Claude SDK replay，已 redacted，並用 `<workspace>`、`<home>`、`<project>` 取代本機路徑。
- `agent_bridge.adapters.fake.FakeAgentAdapter` 直接依賴這些 fixture，移除會破壞離線測試與 CI 可重現性。

## 3. 已刪除的本機殘留

已刪除的類型：

```text
__pycache__/
*.pyc
.pytest_cache/
.mypy_cache/
.ruff_cache/
舊的 ignored core/ pycache 目錄
```

未刪除：

```text
.venv/
.env.local
```

理由：

- `.venv/` 是開發環境，不應在沒有明確授權下刪除。
- `.env.local` 是本機 secret 設定檔，已由 `.gitignore` 忽略；不得讀取或輸出內容。

## 4. `.gitignore` 修訂

已補強：

```gitignore
.coverage
.coverage.*
coverage.xml
htmlcov/
.hypothesis/
.dmypy.json
dmypy.json
.pyre/
.ctfmem/
agent.stop
agent-status.json
*.log
*.pid
*.tmp
```

既有 secret ignore 規則保留：

```gitignore
.env
.env.*
!.env.example
.secrets.env
.secrets.*
```

## 5. 文件與實作同步修正

已修正：

- `README.md`：舊 branch topology 已不符合目前 main 整合狀態，改成說明 Memory Core、Human CLI、Agent Bridge 已整合；新工作應從 current `main` 分支。
- `docs/roadmap.md`：Current Status 從「Human Client / Agent Bridge remain dependent work streams」更新為已整合，並把後續工作定位為 hardening/feature expansion。
- `docs/human-cli.md`：補上 `ctfmem agent run --memory-policy` 的人類可讀值。
- `agent_bridge/loop.py`：移除「agent.stop 路徑還缺答案」的過時註解，改為指向 `.ctfmem/<challenge>/agent.stop`。

## 6. 程式修正

已修正：

- `ctfmem agent run --memory-policy` 新增人類可讀 alias：
  - `full`
  - `facts`
  - `facts-assumptions`
  - `none`
- 舊值仍相容：
  - `fact_only`
  - `fact_and_assumption`
  - `no_memory`
- `ctfmem agent status --json` 對已完成或 stale run 也固定輸出：
  - `runtime_dir`
  - `stop_file`

## 7. 依文件判定的未完成或應修正項

### P1：Agent Bridge 長時間執行的 runtime telemetry 還不完整

目前 `agent-status.json` 會在 run 開始與結束更新。對 YOLO 長時間 run，尚未在每一輪後更新 `turns`、`productive_turns`、`last_revision`、`total_cost_usd`。

建議修正：

- 在 `AgentLoop` 或 CLI orchestration 層加入 per-turn status callback。
- 每輪 ingest 完成後更新 status file。
- 加測 YOLO run 中途 `ctfmem agent status` 可看到進度。

### P1：Agent Bridge tool permission UX 尚未形成穩定 CLI 介面

目前 `ctfmem agent run` 使用預設 `ToolPolicy()`，尚未提供清楚的 `--allow-tool` / `--allow-command` / `--deny-tool` 類選項。

建議修正：

- 先文件化安全預設。
- 再新增最小可驗證的 allow-list CLI。
- 測試 sandbox denied 與 allowed cases。

### P2：Agent stop 狀態語意可更清楚

目前 stop file 會讓 loop 優雅停止，但 status 最終仍以 `completed` 搭配 `stopped_because` 表示原因。對 automation 來說可讀，但語意不夠精確。

建議修正：

- 若 `stopped_because == "stopped"`，status 可設為 `stopped`。
- 保留 `stopped_because` 做詳細原因。

### P2：歷史文件仍保留整合前狀態

以下文件是日期型或 handoff 型歷史文件，保留原文有稽核價值，但可能誤導新讀者：

```text
docs/專案現況-2026-07-26.md
docs/agent-bridge/本次變更-2026-07-26.md
docs/agent-bridge/for-p1-p2.md
```

建議修正：

- 加上「歷史文件，狀態已由 README / roadmap / human-cli 覆蓋」的頁首提示。
- 或移到 `docs/archive/`。

### P2：Live Claude / live AIS3 E2E 仍屬 opt-in

目前 fake Summarizer 與 fake Agent replay 可完整支援離線 CI；真實 Claude 與 AIS3 仍需要本機 credential 與網路。

建議修正：

- 保留 fake tests 作為 CI gate。
- 增加明確的 live test script 或 pytest marker。
- 確保 live test 只檢查語意 invariant，不檢查模型逐字輸出。

### P3：TUI、terminal-session ingestion、PTY recorder 仍是明確未做

文件已把這些列為後續功能或 MVP non-goals：

- TUI / cockpit
- explicit PTY/session recorder
- terminal replay
- background daemon
- shell profile injection / `PROMPT_COMMAND`

目前不應為了「功能完整」而直接實作，需先做設計審查。

## 8. 安全結果

- `.env.local` 未被讀取、未被提交。
- tracked fixture/snapshot 未發現 secret literal。
- Human CLI / Agent Bridge 仍維持不直接使用 AIS3 key；AIS3 key 屬於 Memory Core process。

## 9. 建議下一步

1. 將本次 `.gitignore`、文件同步、小型 CLI 修正與本報告提交到 `feature/ctfmem-agent-cli`。
2. 補做完整 offline validation。
3. 後續另開 hardening commit 處理 Agent Bridge per-turn status callback 與 tool permission UX。
