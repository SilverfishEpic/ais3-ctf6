# Human CLI (`ctfmem`) 規格與驗收文件

## 1. 定位

`ctfmem` 是 Memory Core 的人類端 HTTP client。它負責收集、傳送、顯示與明確人工介入；Memory Core 負責 evidence 保存、語意判定、revision、持久化與 semantic diff。

責任邊界：

```text
Human
  ↓
ctfmem
  ↓ HTTP / OpenAPI
Memory Core
  ↓
Journal → Summarizer → Policy → State
```

## 2. 不可違反規則

- 不直接讀寫 `runs/<challenge>/state.yaml`。
- 不追加 `journal.jsonl`。
- 不配置 Fact / Assumption ID。
- 不修改 Guessing catalog。
- 不自行執行 reclassification。
- 不讀取或使用 `AIS3_API_KEY`、`AIS3_BASE_URL`、`AIS3_SUMMARIZER_MODEL`。
- 普通 `note` 與 explicit `mutate` 是不同命令、不同 API 語意。
- Semantic diff 由 Memory Core 產生，CLI 只 render。

## 3. 套件與入口

正式套件：

```text
human_client/
```

Console entry point：

```bash
ctfmem
```

無參數時只顯示 help，不啟動 shell、不修改 shell profile、不安裝 background recorder。

## 4. 安裝與執行方式

`ctfmem` 是 Python console entry point，由 `pyproject.toml` 的 `[project.scripts]` 宣告。

### 4.1 推薦方式：`uv run`

在 repository root 直接執行：

```bash
uv run ctfmem --help
```

這是 Linux、macOS、Windows PowerShell 都最穩定的用法，因為 `uv run` 會自動使用專案虛擬環境。

### 4.2 Editable install

若要把 entry point 安裝到專案 `.venv`：

```bash
uv pip install -e .
```

安裝後可用：

```bash
uv run ctfmem --help
```

如果直接輸入 `ctfmem` 出現找不到指令，代表 `.venv/bin` 沒在 shell `PATH`。確認位置：

```bash
uv run python -c 'import shutil; print(shutil.which("ctfmem"))'
```

典型輸出：

```text
/path/to/ais3-ctf6/.venv/bin/ctfmem
```

### 4.3 Linux / zsh / bash 直接使用 `ctfmem`

只在目前 shell 啟用：

```bash
source .venv/bin/activate
ctfmem --help
```

或只把目前專案 `.venv/bin` 暫時加入 `PATH`：

```bash
export PATH="$PWD/.venv/bin:$PATH"
ctfmem --help
```

不建議把某個專案的 `.venv/bin` 永久寫死到全域 `~/.zshrc`，避免在其他專案誤用這份 venv。若需要自動載入，建議使用 `direnv`。

### 4.4 Windows PowerShell 直接使用 `ctfmem`

在 repository root：

```powershell
uv pip install -e .
uv run ctfmem --help
```

若要啟用 venv 後直接輸入 `ctfmem`：

```powershell
.\.venv\Scripts\Activate.ps1
ctfmem --help
```

若 PowerShell 阻擋啟用 script，需調整目前使用者的 execution policy：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

也可以不啟用 venv，直接呼叫完整路徑：

```powershell
.\.venv\Scripts\ctfmem.exe --help
```

## 5. 啟動 Memory Core server

`ctfmem` 只連線到 Memory Core HTTP API。AIS3 key 只屬於 Memory Core server process，不應提供給 `ctfmem`。

### 5.1 Linux / zsh / bash

```bash
set -a
. ./.env.local
set +a

uv run python -m memory_core \
  --runs ./runs \
  --templates ./templates \
  --host 127.0.0.1 \
  --port 8000
```

### 5.2 Windows PowerShell

PowerShell 不會直接 source POSIX `.env`。可用下列方式載入簡單的 `KEY=value` 格式：

```powershell
Get-Content .env.local | ForEach-Object {
  if ($_ -match '^\s*([^#][^=]+)=(.*)$') {
    [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim(), "Process")
  }
}

uv run python -m memory_core `
  --runs ./runs `
  --templates ./templates `
  --host 127.0.0.1 `
  --port 8000
```

## 6. Linux / Windows 基本使用

另開一個 console 設定 Human CLI 連線資訊。

Linux / zsh / bash：

```bash
export CTFMEM_API_URL=http://127.0.0.1:8000
export CTFMEM_CHALLENGE=demo-web
export CTFMEM_ACTOR=ctshen
```

Windows PowerShell：

```powershell
$env:CTFMEM_API_URL = "http://127.0.0.1:8000"
$env:CTFMEM_CHALLENGE = "demo-web"
$env:CTFMEM_ACTOR = "ctshen"
```

建立 challenge：

```bash
uv run ctfmem challenge create demo-web --title "Demo Web"
```

送出筆記：

```bash
uv run ctfmem note "I ran nmap and 80/tcp is open."
```

stdin 筆記：

Linux / zsh / bash：

```bash
printf '%s\n' "The login page sets a sid cookie." | uv run ctfmem note -
```

Windows PowerShell：

```powershell
"The login page sets a sid cookie." | uv run ctfmem note -
```

執行並擷取外部命令：

```bash
uv run ctfmem capture -- curl -i http://target/login
```

Windows PowerShell 使用系統 curl 時建議明確呼叫 `curl.exe`：

```powershell
uv run ctfmem capture -- curl.exe -i http://target/login
```

查看 memory：

```bash
uv run ctfmem show
```

查詢：

```bash
uv run ctfmem query "目前已確認哪些服務？"
```

Guessing：

```bash
uv run ctfmem guess --count 3 --seed 42 --only-untried
```

人工 mutation：

```bash
uv run ctfmem mutate add assumption \
  --section attack_paths \
  --text "The sid cookie may be weakly signed." \
  --reason "Need manual testing" \
  --revision 1
```

Windows PowerShell：

```powershell
uv run ctfmem mutate add assumption `
  --section attack_paths `
  --text "The sid cookie may be weakly signed." `
  --reason "Need manual testing" `
  --revision 1
```

JSON mode：

```bash
uv run ctfmem --json show
uv run ctfmem --json query "目前有哪些 Fact？"
```

## 7. Agent Bridge 入口

`ctfmem agent` 是 Human CLI 對 Agent Bridge 的統一入口。Human CLI 只負責
CLI parsing、設定與輸出；實際 agent 編排仍由 `agent_bridge.AgentLoop` 與
adapter 實作。

實際路徑：

```text
ctfmem agent run
  ↓
AgentLoop
  ↓
ClaudeAdapter、CodexAdapter 或 FakeAgentAdapter
  ↓
normalized run log
  ↓
Memory Core /ingest
```

離線 fake-agent 驗證：

```bash
uv run ctfmem --challenge demo-web agent run \
  --provider fake \
  --fake-agent multi_tool \
  "繼續分析"
```

為了相容既有指令，省略 `--provider` 但提供 `--fake-agent` 時也會使用 fake
provider。

真 Claude normal run：

```bash
uv run ctfmem --challenge demo-web agent run \
  --provider claude \
  --workspace . \
  --mode normal \
  --session same \
  "繼續分析"
```

Claude 是目前預設 provider。若省略 `--provider` 且沒有 `--fake-agent`，等同於
`--provider claude`，除非環境變數 `CTFMEM_AGENT_PROVIDER` 指定其他值。

Codex MVP normal run：

```bash
uv run ctfmem --challenge demo-web agent run \
  --provider codex \
  --workspace . \
  --mode normal \
  --session same \
  "繼續分析"
```

Codex 目前透過 `codex exec --json` 非互動模式執行。這個 MVP 刻意保守：

```text
[x] 支援 normal run
[x] 支援 YOLO run
[x] 支援 session=same
[x] 支援 session=new
[x] 將 Codex JSONL events normalize 成 AgentRunEvent
[x] unknown provenance 以 outcome=unknown 保存
[x] 支援唯讀 Codex MCP memory skill
[x] Codex subprocess 會移除 AIS3 / Memory Core provider env
[x] Codex run 會固定使用 untrusted approval policy、忽略 user config 與 project rules，並固定使用 read-only sandbox
[x] Codex 與 Claude 達到相同保守權限等級，但 enforcement mechanism 不同
```

Codex 若要使用和 Claude 類似的跨輪上下文，使用 `--session same`。若要每輪只依賴
Memory Core 白板，使用 `--session new`。

預設會依照 memory policy 啟用唯讀 memory skill，提供：

```text
memory_show
memory_query
memory_guess
```

若要完全關閉 memory skill，只保留 prompt board injection，可加上
`--no-memory-skill`。

Codex path 會強制加入 `--ask-for-approval untrusted`、`--ignore-user-config`、
`--ignore-rules`、`--sandbox read-only`，並在啟動 Codex subprocess 前移除
`AIS3_API_KEY`、`AIS3_BASE_URL`、
`AIS3_SUMMARIZER_MODEL`、`AIS3_TIMEOUT` 等 Memory Core provider credentials。
這表示 Codex 可以讀白板，但不應取得 AIS3 key。

YOLO run：

```bash
uv run ctfmem --challenge demo-web agent run \
  --mode yolo \
  --session same \
  --max-turns 10 \
  --max-cost-usd 5 \
  --idle-turns 3 \
  "把題目解完"
```

Codex YOLO run：

```bash
uv run ctfmem --challenge demo-web agent run \
  --provider codex \
  --mode yolo \
  --session same \
  --max-turns 5 \
  --idle-turns 2 \
  "把題目解完"
```

Memory exposure policy：

```bash
uv run ctfmem --challenge demo-web agent run \
  --memory-policy facts-assumptions \
  "根據目前記憶繼續分析"
```

建議使用的人類可讀值：

```text
full
facts
facts-assumptions
none
```

舊的內部值 `fact_only`、`fact_and_assumption`、`no_memory` 仍保留相容。

Stop / status：

```bash
uv run ctfmem --challenge demo-web agent stop
uv run ctfmem --challenge demo-web agent status
```

第一版不啟動 daemon。`agent stop` 寫入 challenge-local stop file，`agent run`
在 loop 中偵測後優雅停止，已完成的 partial events 仍會送進 Memory Core。

預設 runtime 目錄：

```text
.ctfmem/<challenge>/
├── agent.stop
└── agent-status.json
```

`.ctfmem/` 是 Human CLI / Agent Bridge 的本機 runtime metadata，不屬於 Memory
Core persistence，也不應 commit。

JSON mode：

```bash
uv run ctfmem --json --challenge demo-web agent run \
  --provider fake \
  --fake-agent single_tool \
  "查一下"
```

## 8. 全域參數與環境變數

全域參數：

```text
--api-url URL
--challenge ID
--actor NAME
--json
--timeout SECONDS
--verbose
--no-color
```

環境變數：

```bash
CTFMEM_API_URL=http://127.0.0.1:8000
CTFMEM_CHALLENGE=demo-web
CTFMEM_ACTOR=ctshen
CTFMEM_TIMEOUT=30
```

解析優先序：

```text
CLI argument > environment variable > documented default / error
```

沒有 challenge 的命令必須明確報錯，不可默默套用未知題目。

## 9. 命令樹

```text
ctfmem
├── challenge
│   ├── create
│   ├── list
│   └── show
├── note
├── capture
├── show
├── query
├── guess
├── mutate
│   ├── add
│   ├── replace
│   ├── remove
│   ├── promote
│   ├── demote
│   └── move
├── guessing
│   └── mark-tried
├── template
│   └── set
└── agent
    ├── run
    ├── stop
    └── status
```

## 10. I/O 語意

### `note`

```bash
ctfmem --challenge demo-web note "I found /admin."
printf '%s\n' "The JWT uses HS256." | ctfmem --challenge demo-web note -
ctfmem --challenge demo-web note --stdin
```

送到 Memory Core：

```json
{
  "source": {"type": "human", "actor": "ctshen"},
  "input": {"type": "note", "content": "..."}
}
```

### `capture`

```bash
ctfmem --challenge demo-web capture -- nmap -sC -sV target
```

要求：

- child stdout 即時 tee 到目前 stdout。
- child stderr 即時 tee 到目前 stderr。
- 程序結束後只送出一個完整 `command_result` ingest。
- 不使用 `shell=True` / `os.system()`。
- 非零 child exit code 不代表 ingest 失敗；JSON output 需保留 `child_exit_code` 與 ingest result。
- `--json` 模式為維持 stdout 單一合法 JSON document，child stdout/stderr 會 tee 到 stderr。

### `show`

必須保留：

- revision
- challenge metadata
- template/version
- Fact / Assumption 分層
- section
- item ID
- evidence references
- Guessing tried state

不得把 Fact 與 Assumption 混成一般 note list。

### `query`

CLI 不自行生成答案，只顯示 Memory Core 回傳的 answer / references / revision。

### `guess`

Guess candidates 必須來自 active template；同 seed 必須 deterministic；CLI 不得產生 free-form Guess。

### `mutate` / `template set`

所有 mutation 必須帶：

```text
expected_revision
actor
reason
```

收到 409 時停止，不自動抓新 revision 重送。

## 11. JSON output

所有主要命令支援 `--json`。

要求：

- stdout 只輸出單一合法 JSON document。
- log / error 放 stderr。
- 不輸出 spinner、提示文字或 ANSI。
- 保留原始 OpenAPI 欄位名稱。
- error JSON 可供 script 判斷。

## 12. Exit code contract

| Exit code | 意義 |
|---:|---|
| `0` | 成功 |
| `2` | CLI usage／本機輸入錯誤 |
| `3` | 無法連線至 Memory Core／timeout |
| `4` | challenge、item 或 template 不存在 |
| `5` | revision conflict |
| `6` | request validation error |
| `7` | Core／Summarizer／provider server error |
| `8` | 本機 command capture 啟動失敗 |

## 13. MVP 不做

- shell profile injection
- `PROMPT_COMMAND` interception
- background command watcher
- 第二份 `rounds.jsonl`
- full PTY recorder
- terminal session replay
- background summarization daemon
- local Fact/Assumption store
- direct YAML patch
- built-in autonomous Agent loop
- local template catalog editing
- direct AIS3 calls

## 14. 驗收準則

- `uv run --extra dev pytest`
- `uv run --extra dev ruff check .`
- `uv run --extra dev mypy memory_core human_client`

必要測試覆蓋：

- HTTP-only note ingest。
- command_result capture 保留 argv / exit code / stdout / stderr。
- JSON stdout 可 parse。
- stale mutation 回傳 exit code `5`。
- `show` 保留 Fact / Assumption / Guessing 結構。
- seed guessing deterministic。
- Human Client 不 import `memory_core`。
- Human Client 不讀寫 `state.yaml` / `journal.jsonl`。
- Human Client 不讀 AIS3 secret。
