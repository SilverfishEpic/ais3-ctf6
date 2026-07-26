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

## 4. 全域參數與環境變數

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

## 5. 命令樹

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
└── template
    └── set
```

## 6. I/O 語意

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

## 7. JSON output

所有主要命令支援 `--json`。

要求：

- stdout 只輸出單一合法 JSON document。
- log / error 放 stderr。
- 不輸出 spinner、提示文字或 ANSI。
- 保留原始 OpenAPI 欄位名稱。
- error JSON 可供 script 判斷。

## 8. Exit code contract

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

## 9. MVP 不做

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

## 10. 驗收準則

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
