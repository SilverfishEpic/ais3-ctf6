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

模型、endpoint、timeout、runs 和 templates 路徑可放進被 git 忽略的
`.ctfmem/core-config.json`；第一次只需初始化一次。AIS3 API key 是唯一必須保留在
server process environment 的設定。

### 5.1 Linux / zsh / bash

```bash
./scripts/memory-core-init.sh

AIS3_API_KEY='...' uv run python -m memory_core --host 127.0.0.1 --port 8000
```

### 5.2 Windows PowerShell

```powershell
.\scripts\memory-core-init.ps1 -Model "ais3/nemotron-3-ultra-550b"
# Make AIS3_API_KEY available only to this server process via your approved secret mechanism.

uv run python -m memory_core --host 127.0.0.1 --port 8000
```

Use your organisation's secret manager or set `AIS3_API_KEY` only for that process. The important
boundary is that neither init script writes a key to disk.

## 6. Linux / Windows 基本使用

## 原生終端 slash 操作面

`ctfmem` 不會接管終端或啟動全螢幕 UI；slash 命令就是一般 CLI 子命令，因此 shell
原有的 history、completion、plugin 與快捷鍵都照常運作。Web UI 的操作功能可用文字輸出
完成，但不嘗試複製動畫或輪詢畫面。

```bash
ctfmem /challenge list
ctfmem /challenge create web-01 --title 'Web 01' --template web
ctfmem /challenge use web-01
ctfmem /memory --facts --section recon
ctfmem /note 'port 80 is open'
cap curl -i http://target/
ctfmem /command --command 'nmap -sV target' --exit-code 0 --stdout '...'
ctfmem /add fact --text 'TLS is enabled' --section recon --reason 'confirmed with curl' --tag tls
ctfmem /edit add fact --text 'TLS is enabled' --reason 'confirmed with curl'
ctfmem /template set web --reason 'service classification changed'
ctfmem /guess --count 1 --seed 7
ctfmem /try sqli --note 'parameter is escaped' --reason 'tested manually'
ctfmem /ask 'Which ports are open?' --fact
```

`ctfmem shell-init --shell zsh` installs a static, marked `cap` function into `~/.zshrc`; open a
new zsh afterwards. It refuses to overwrite an existing `cap` command, function, or alias, and
the installed code checks again at shell startup. `ctfmem shell-init --remove` removes only its
own marker block; `--print` displays that fixed block without changing a startup file.

`cap` is a short explicit wrapper for `ctfmem capture --`. It captures only that command's
stdout/stderr and exit code; it does not observe unrelated commands or proxy interactive
terminal input. It is for external programs such as `curl`, `nmap`, and `checksec`: shell
builtins (`cd`, `export`, aliases) cannot change the parent shell through `cap`. Do not use it
for prompt-driven programs such as `gdb` or `sqlmap`; their later terminal input is deliberately
not recorded.

`/edit`、`/try` 和 `/template set` 在省略 `--revision` 時會先讀取目前 revision；腳本仍可
明確提供 `--revision`。revision conflict 絕不重試：`ctfmem` 會顯示目前白板並以 conflict
exit code 結束，讓操作員自行檢閱。互動式 TTY 中缺少 challenge id、reason 或 agent prompt
等單一值時會逐欄詢問；pipe stdin 不會詢問，而是回傳 usage error。多行 note 使用
`/note --stdin`；`/command --stdin` 接收含 `command`、`exit_code`、`stdout`、`stderr` 的
JSON 物件（也可用 `--stdin stdout` / `--stdin stderr` 輸入單一 stream）。

## Agent workspace

Agent work is enabled only after explicitly naming a directory. This avoids silently placing
agent output beside Memory Core state or a challenge solution:

```bash
ctfmem /agent init ./agent-work
ctfmem /agent run 'inspect the binary' --mode normal
ctfmem /agent status
ctfmem /agent stop
```

`/agent init` creates the directory, resolves and saves its absolute path in the non-secret
project config. `ctfmem setup --agent-workspace PATH` and `ctfmem config set
--agent-workspace PATH` write the same setting. The existing long-form `ctfmem agent run
--workspace PATH ...` remains compatible.

Human CLI 的日常設定不必再寫入 environment。第一次為一個專案設定即可；它只會寫入
被 git 忽略的 `.ctfmem/client-config.json`，內容為 Core URL、challenge、actor、timeout。
它**不會**保存 API key、模型或其他 AIS3 憑證。

Linux / zsh / bash：

```bash
./scripts/ctfmem-init.sh
# 非互動式（自動化）設定：
ctfmem setup --no-input --api-url http://127.0.0.1:8000 --challenge demo-web --actor ctshen
```

Windows PowerShell：

```powershell
.\scripts\ctfmem-init.ps1 -Challenge demo-web -Actor ctshen
# 也可直接使用：ctfmem setup
```

檢視目前設定：

```bash
ctfmem config show
```

命令列的 `--api-url`、`--challenge`、`--actor`、`--timeout` 永遠優先；其次才是
專案 JSON 設定。`CTFMEM_*` environment variables 不再讀取。可用
`--config path/to/config.json` 選用另一份非機密設定檔。

AIS3 的 API key 必須仍由 Memory Core server process 的 environment 提供。模型、base URL、
timeout 是 server-side 設定，絕不會傳給或寫入 human CLI。

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
ClaudeAdapter 或 FakeAgentAdapter
  ↓
normalized run log
  ↓
Memory Core /ingest
```

離線 fake-agent 驗證：

```bash
uv run ctfmem --challenge demo-web agent run \
  --fake-agent multi_tool \
  "繼續分析"
```

真 Claude normal run：

```bash
uv run ctfmem --challenge demo-web agent run \
  --workspace . \
  --mode normal \
  --session same \
  "繼續分析"
```

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
  --fake-agent single_tool \
  "查一下"
```

## 8. 全域參數與設定來源

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

解析優先序：

```text
CLI argument > .ctfmem/client-config.json > documented default / error
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
- `stdout`/`stderr` 是可顯示的 UTF-8 replacement preview；`stdout_base64`/`stderr_base64`
  同時保存原始 bytes，因此非 UTF-8 輸出不會令 capture 中斷或遺失。
- 不使用 `shell=True` / `os.system()`。
- 非零 child exit code 不代表 ingest 失敗；JSON output 需保留 `child_exit_code` 與 ingest result。
- `--json` 模式為維持 stdout 單一合法 JSON document，child stdout/stderr 會 tee 到 stderr。
- 只適用於會自行結束的命令；不攔截或記錄 child 的後續互動 stdin。

每次 ingest 前，capture 都會先在 `.ctfmem/pending-captures.json` 寫入完整結果。若 Core
連線或回應失敗，結果會保留並印出 pending ID；它**不會**自動重送，因為斷線時客戶端無法
確定伺服器是否已收到原請求。操作員可先查看再明確重送：

```bash
ctfmem capture-pending list
ctfmem capture-pending retry PENDING_ID
```

retry 成功才會移除本機項目；若原請求其實已送達，重送可能建立另一筆 journal record，因此
應先依 ID 與 Core 白板／journal 狀態判斷。

### `add`

`ctfmem add` 與 `/add` 是 Web UI「新增 Fact／Assumption」表單的終端入口。可直接帶齊
欄位，也可在 TTY 中逐欄選擇 layer、section、text、reason；section 會以編號與名稱列出。

```bash
ctfmem --challenge demo-web /add fact --text 'port 443 is open' \
  --section recon --reason 'nmap result I-0003' --evidence I-0003 --tag network
```

`--evidence` 和 `--tag` 都可重複指定。`/edit add`、`mutate add` 與 `replace` 同樣支援
這兩個欄位；replace 可用 `--clear-evidence` 或 `--clear-tags` 明確清空其中一項，讓腳本和
Web UI 使用相同的 mutation 資料。

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
