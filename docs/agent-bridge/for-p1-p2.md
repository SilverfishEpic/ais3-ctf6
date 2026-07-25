# 給 Person 1 / Person 2：不用 Claude 就能拿到 IngestEnvelope

`FakeAgentAdapter` 重播的是**真的 Claude 錄音**，不是編出來的假資料。
你們不需要裝 Claude、不需要 API key、不會花錢，也不需要等 contract freeze。

```bash
python tools/demo_fake_adapter.py                    # 六份錄音的摘要
python tools/demo_fake_adapter.py multi_tool --json  # 完整 envelope
```

```python
from agent_bridge.adapters.fake import FakeAgentAdapter

envelope = FakeAgentAdapter("multi_tool").run_sync()
# 這就是 POST /ingest 的 body
```

---

## 六份錄音各自的用途

| 錄音 | 內容 | 為什麼要有它 |
|---|---|---|
| `talk_only` | 只有文字，沒有工具 | 最單純的形狀 |
| `single_tool` | 一次工具，成功 | 最小完整形狀 |
| `multi_tool` | 多輪工具，全部成功 | 排序與 tool_use_id 配對 |
| `tool_failure` | 指令跑了但 exit 非 0 | 失敗**是**關於題目的證據 |
| `sandbox_denied` | 被沙箱擋下，指令沒執行 | **不是**關於題目的證據 |
| `interrupted` | 中途被喊停 | 什麼都不能推論 |

**後兩份特別重要。** 如果 Summarizer 把 `sandbox_denied` 的 tool_result 當成一般證據，
白板上會出現「rm 不在白名單」這種跟題目完全無關的 Fact。

---

## 事件形狀

依規劃書 §8.2，加上三個我依實測提出的欄位（尚未 freeze，歡迎打槍）。

```json
{
  "seq": 3,
  "role": "tool",
  "kind": "tool_result",
  "name": "Bash",
  "tool_use_id": "toolu_01PV2JL...",
  "content": "total 2\ndrwxr-xr-x ...",
  "outcome": "ok",
  "duration_ms": 781.0,
  "truncated": true,
  "original_bytes": 184320
}
```

`role` ∈ `assistant` / `tool` / `system`
`kind` ∈ `message` / `tool_call` / `tool_result` / `notice`

### 三個提案欄位

**1. `outcome`（新提案）** —— 四種結局對 Summarizer 的意義完全不同：

| 值 | 意思 | Summarizer 該怎麼看 |
|---|---|---|
| `ok` | 指令跑完 | 正常證據 |
| `failed` | 跑了但 exit 非 0 | **是**證據（「這條路不通」也是資訊） |
| `denied` | 被沙箱擋下，**沒執行** | **不是**證據，別寫進白板 |
| `interrupted` | 人喊停，沒執行 | **不是**證據 |

沒有這個欄位，`denied` 和 `failed` 從外觀上分不出來（兩者的 `is_error` 都是 true）。

**2. `truncated` / `original_bytes`（contract 第 2 題）** ——
`nmap -A`、`strings` 一個 binary 隨便就幾百 KB，我這邊會截斷。
但 journal 是「原始證據紀錄」，砍了不說就是說謊，所以每筆被砍過的都帶這兩個欄位。

**3. `input`（contract 第 4 題）** —— `tool_call` 事件同時給兩份：
`content` 是人類可讀的一行（`ls -la`），`input` 是結構化的真相（`{"command": "ls -la", ...}`）。
§8.2 的範例把指令塞進 `content` 字串，但 Bash 以外的工具參數是 dict，字串塞不下。

---

## `run_meta`：不是證據的東西放這裡

```json
{
  "model": "claude-opus-4-8",
  "session_id": "...",
  "num_turns": 3,
  "duration_ms": 9217,
  "total_cost_usd": 0.083,
  "stop_reason": "end_turn",
  "terminal_reason": "completed",
  "interrupted": false,
  "thinking_blocks": 2,
  "tool_calls": 2,
  "usage": { }
}
```

這是 **contract 第 3 題的具體提案**：成本、耗時、token 數不該混進 `events[]`，
它們不是關於題目的證據。目前放在 `input.run_meta`。

`thinking_blocks` 只有數量沒有內容 —— 實測 Claude 的 thinking 內容一律不回傳
（見 `m0-findings.md` §2）。

---

## 我需要你們回答的

**P1：**

1. 路徑是 `/ingest` 還是 `/v1/challenges/{id}/ingest`？兩份設計文件寫得不一樣
2. `truncated` / `original_bytes` 收不收？
3. `run_meta` 放 `input` 底下可以嗎？還是要另外開一層？
4. `tool_call` 的 `input` 欄位收不收？
5. `outcome` 收不收？（我認為這是這幾個裡面最重要的）

**P2：**

6. `runs/<題目>/agent.stop` 這個中止旗標放哪、誰寫誰刪？

前五題答完之前，我這邊的欄位都可以改，不用等。
