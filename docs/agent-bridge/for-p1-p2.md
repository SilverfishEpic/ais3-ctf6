# 給 Person 1 / Person 2

## 現況：已經對得上 contract 了

`spec/openapi.yaml`（`origin/memory-system`）我照著改完了，六份錄音全部通過
Memory Core 自己的驗證：

```bash
# 你那邊
python -m memory_core --fake-summarizer --runs ./runs --templates ./templates --port 8777

# 我這邊
python tools/check_against_memory_core.py --base-url http://127.0.0.1:8777
# → 6/6 份全部被 Memory Core 接受
```

不用等我，也不用裝 Claude：

```python
from agent_bridge.adapters.fake import FakeAgentAdapter
envelope = FakeAgentAdapter("multi_tool").run_sync()   # 這就是 /ingest 的 body
```

六份錄音重播的是**真的 Claude 執行紀錄**，不是編的。編出來的乾淨資料會讓
Summarizer 對著現實中不存在的理想輸入調 prompt。

| 錄音 | 內容 |
|---|---|
| `talk_only` | 只有文字，沒有工具 |
| `single_tool` | 一次工具，成功 |
| `multi_tool` | 多輪工具，全部成功 |
| `tool_failure` | 指令跑了但 exit 非 0 |
| `sandbox_denied` | 被沙箱擋下，**指令從未執行** |
| `interrupted` | 中途被喊停，**指令從未執行** |

---

## 給 P1：R2 有一個洞，我先從我這邊補起來了

### 問題

`memory_core/summarizer.py` 的 `is_direct_evidence()`：

```python
if record.input_type == "run_log":
    events = (record.content or {}).get("events", [])
    return any(event.get("kind") == "tool_result" for event in events)
```

**它只看有沒有 `tool_result`，不看那個工具有沒有真的執行過。**

Agent 的工具呼叫有兩種情況會產生 `tool_result`、但指令**從來沒有跑**：

1. 被沙箱的 `PreToolUse` 白名單擋下
2. 被人中止（`client.interrupt()`）

兩種情況 SDK 都會回一個 `ToolResultBlock`（`is_error=True`），內容是拒絕理由或
中斷訊息。如果照原樣當 `kind: tool_result` 送給你，那份 run_log 就取得了
「直接證據」資格，Summarizer 就被允許從中產生 Fact ——
而那個內容是「`rm` 不在白名單內」，跟題目一點關係都沒有。

### 證據

用你的程式碼跑的對照實驗（`is_direct_evidence` 直接呼叫，同一個被擋的 `rm` 指令）：

```
A  denial 送成 system/message   → is_direct_evidence = False
B  denial 送成 tool_result      → is_direct_evidence = True
```

### 我的處理

不需要你改任何東西。**沒有執行過的工具呼叫，我不用 `kind: tool_result` 送出去**，
改成 `role: system` + `kind: message`，內容前面加標記：

```
[not executed: blocked by sandbox] Bash: 指令 rm 不在白名單內…
[not executed: interrupted by user] Bash: …
```

語意上也更誠實 —— 那個指令沒有產生任何 tool result。

**指令真的跑了但失敗（exit 非 0）仍然是 `tool_result`**，因為那是關於題目的真證據
（「這條路不通」也是資訊）。`tool_failure` 那份錄音就是這個情況。

### 但這只是我這邊補起來

任何**別的** Agent Bridge 實作、或未來換 provider，都會踩同一個洞。
如果你想在核心擋住，可以考慮：

- `AgentRunEvent` 加一個 optional 的 `outcome`（`ok` / `failed` / `denied` / `interrupted`），
  R2 只認 `ok` 與 `failed`
- 或者 R2 不只看 `kind`，也看內容有沒有被標記為未執行

`protocol.md` §13 寫「Optional fields may be added later」，所以這是可以談的。
不急 —— 我這邊已經不會送出髒資料了。

---

## 我為了合約丟掉的東西（留在本地，沒上線）

`AgentRunEvent` 是 `additionalProperties: false`，所以這些只留在
`NormalizedRun.run_meta` 給我自己的 loop 用：

| 丟掉的 | 我本來想幹嘛 | 現在怎麼辦 |
|---|---|---|
| `outcome` | 見上面 | 改用事件形狀表達 |
| `truncated` / `original_bytes` | 讓 journal 在內容被截斷時仍誠實 | 截斷標記直接寫在 `content` 裡：`…[truncated N bytes]…` |
| `input`（結構化工具參數） | Bash 以外的參數是 dict | 攤成 `content` 的可讀字串 |
| `tool_use_id` | 配對 tool_call / tool_result | 只靠事件順序 |
| `duration_ms` | 工具耗時 | 只留本地當 telemetry |
| `run_meta`（成本／turns／stop_reason） | `RunLogInput` 沒位置 | 只留本地 |

這些都不擋事，列出來只是讓你知道我有哪些資訊、之後如果要開 optional 欄位可以直接拿。

---

## 模板：分工要確認

`protocol.md` §1 寫 **Person 3 owns real technique catalogs**，但 `origin/template`
branch 已經有 pwn / rev / crypto / web 了。我這邊也做了 generic + web。

**重複了，需要決定誰做。** 在確認之前我不動 `templates/`。

另外一點技術問題：`template.schema.json` 規定 guess ID 是
`^[A-Z0-9]+-[A-Z0-9-]+$`，**強制要有前綴段**。

我原本的想法是 ID 用技術本身命名（`SQLI` 而不是 `WEB-SQLI`），因為 §8 說重新分類時
要「carry `tried` state for technique IDs that still exist」—— 前綴會讓同一個技術在
不同題型下變成不同 ID，題型判錯一次再改回來，試過的紀錄就掉了。

schema 目前不允許這種寫法。不是大問題（可以在兩份模板裡放同一個
`WEB-SQLI`），但值得確認這是不是刻意的。

---

## P2

`agent.stop` 這個中止旗標放哪、誰寫誰刪？這是我 M3 唯一還缺的東西。
