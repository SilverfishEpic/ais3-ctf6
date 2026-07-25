# M0 觀察紀錄：Claude Agent SDK 實際吐出什麼

> 來源：`fixtures/claude/*.jsonl`，用 `tools/record_run.py` 對真的 SDK 錄下來的。
> 環境：`claude-agent-sdk` 0.2.128、Claude Code 2.1.x、模型 `claude-opus-4-8`（CLI 預設）、Windows。
> M1 寫 `normalize.py` 以這份為準，不要靠推測。

錄之前寫在腳本裡的假設，有兩條被實測推翻。這就是 M0 存在的理由。

---

## 1. 實際出現的訊息型別

一輪執行會看到這些（`ClaudeSDKClient.receive_response()`）：

| 型別 | 說明 |
|---|---|
| `SystemMessage` (`subtype=init`) | 開場，帶 cwd / model / tools / session_id / permissionMode |
| `SystemMessage` (`subtype=thinking_tokens`) | 思考中的進度訊號，帶 `estimated_tokens`，會出現很多次 |
| `AssistantMessage` | 內容是 block 陣列：`ThinkingBlock` / `TextBlock` / `ToolUseBlock` |
| `UserMessage` | 工具結果回填，內容含 `ToolResultBlock` |
| `RateLimitEvent` | **官方 hook 文件沒列**，但實際會出現，帶 `rate_limit_info` |
| `ResultMessage` | 收尾，帶 cost / duration / turns / stop_reason / terminal_reason |

`normalize.py` 必須對未知型別採取「跳過但記一筆」的態度，不能假設型別是封閉集合。

## 2. 思考內容拿不到 —— 實測確認

每個 `ThinkingBlock` 都是 `thinking=""`（長度 0）、`signature` 有值。

也就是說白板只可能拿到「它想了」這個事實，拿不到推理內容。這與規劃書 §4 相容：
Agent 的推論本來就只能成為 Assumption，真正的證據來自工具輸出。

## 3. hook 與 message stream 的 `tool_use_id` 完全對得上

`PostToolUse` hook 的 `tool_use_id` == `AssistantMessage` 裡 `ToolUseBlock.id`
（例如 `toolu_01PV2JLgfsXT8oWzY4pJCERf`）。兩邊可以直接合併，不需要靠順序猜。

`PreToolUse` → `PostToolUse` 的配對也用同一個 id，所以 `duration_ms` 算得出來（實測 719～797ms）。

## 4. hook 拿到的資料比 stream 豐富

同一次 `ls -la`：

- **hook** 的 `tool_response`：結構化 dict —— `{'stdout': '...', ...}`
- **stream** 的 `ToolResultBlock.content`：壓平成一個字串

這是決定 1（用 hook 收集）除了時間戳之外的第二個實質理由。

## 5. 三種「不正常結束」的形狀都不一樣

M1 的轉換必須分得出這三種，它們對 Summarizer 的意義完全不同。

### 5.1 工具執行失敗（指令跑了，但 exit 非 0）

```
hook/PostToolUseFailure
    error = "Exit code 1\ncat: does_not_exist_xyz.txt: No such file or directory"
    is_interrupt = False
message/UserMessage → ToolResultBlock(is_error=True, content=同上字串)
message/ResultMessage → subtype=success, terminal_reason=completed
```

**注意整輪仍是 `success`** —— 工具失敗不等於執行失敗。

### 5.2 被沙箱擋下（指令根本沒跑）

```
（沒有任何 hook 紀錄 —— 這就是證明它沒執行）
message/UserMessage → ToolResultBlock(is_error=True, content=我寫的拒絕理由，原樣)
message/ResultMessage → subtype=success, terminal_reason=completed
                        permission_denials=[{tool_name, tool_use_id, tool_input}]
```

`permissionDecisionReason` 會**原封不動**變成 Agent 看到的 tool result，所以理由要寫給模型看，
不能只寫給人看。實測 Agent 讀懂了並照指示沒有重試。

工作區檔案在錄完後完整無缺，這是指令沒執行的實體證明。

### 5.3 中途取消（`client.interrupt()`）

**原本的假設錯了**：不會有 `PostToolUseFailure`，也沒有 `is_interrupt=True`。
因為被打斷的那個工具根本還沒開始執行。

```
（被打斷的工具沒有 hook 紀錄；它之前已完成的工具有）
message/UserMessage → ToolResultBlock(is_error=True,
                        content="The user doesn't want to proceed with this tool use...")
message/UserMessage → TextBlock("[Request interrupted by user for tool use]")
message/ResultMessage → subtype=error_during_execution
                        is_error=True
                        stop_reason=tool_use
                        terminal_reason=aborted_tools     ← 見下方警告
```

> ⚠️ **`terminal_reason` 不是固定值。** 同一個情境錄兩次，一次是 `aborted_tools`
> （中斷落在工具執行階段），一次是 `aborted_streaming`（落在串流階段）。
> 不要寫死比對其中一個。
>
> `normalize.py` 改成兩個訊號互補：`terminal_reason` 以 `aborted` 開頭，
> **或**任何一個事件的 `outcome` 是 `interrupted`（那個是靠 tool_result 內容
> 的固定字串判斷的，比較穩）。

**對 M3 的意義**：規劃書 §14 要求「已完成的 log 仍盡量 ingest」。這做得到 ——
中斷前跑完的工具在 collector 的 buffer 裡（實測 `ls` 那筆有留下），
`collector.drain()` 拿得到。

## 6. `ResultMessage` 就是 contract 第 3 題的答案

它帶了 `total_cost_usd`、`duration_ms`、`duration_api_ms`、`num_turns`、`usage`、
`model_usage`、`stop_reason`、`terminal_reason`、`permission_denials`。

這些**不是證據**，不該混進 `events[]`，應該另開一個 `run_meta` 欄位。
向 P1 提問時可以直接拿這份欄位清單。

## 7. 成本參考

六個情境錄一輪的實際花費：$0.06 ～ $0.18 不等，全部合計約 $0.6。
重錄很便宜，不要為了省這個而對著假資料寫轉換。
