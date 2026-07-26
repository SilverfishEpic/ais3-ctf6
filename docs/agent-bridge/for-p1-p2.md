# 給 Person 1 / Person 2

## 現況:整條線通了,而且有一支腳本可以證明

```bash
python -m memory_core --fake-summarizer --runs ./runs --templates ./templates --port 8777
python tools/integration_check.py --base-url http://127.0.0.1:8777
```

這支跑的是**起點文件 §10 的 11 步**,打真的 HTTP。最近一次結果:

```
 1. 建立 challenge                      revision=0 template=web
 2. Human note 送進 ingest              「I ran nmap and port 80 is open.」
 3. Summarizer 產生 Fact 與 Assumption   F001  TCP/80 is open.
                                        A001  The target may expose an HTTP application.
 4. revision 0 → 1
 5. 白板 render 得出來                   284 字元
 6. Agent 拿到白板(bridge 強制注入)      540 字元
 7. Agent 回 normalized run log          7 個事件,2 個 tool_result,2 個算直接證據
 8. revision 1 → 2
 9. Human promote 成功                   rev 2 → 3  promoted=['A001']
10. stale revision 被擋下                409 revision_conflict
11. seed=42 兩次結果相同,換 seed 會變
```

第 3 步跑出來的 F001 / A001 跟起點文件 §10 寫的**一字不差**。

不用等我,也不用裝 Claude:

```python
from agent_bridge.adapters.fake import FakeAgentAdapter
envelope = FakeAgentAdapter("multi_tool").run_sync()   # 這就是 /ingest 的 body
```

`fixtures/claude/` 六份重播的是**真的 Claude 執行紀錄**,不是編的 ——
編出來的乾淨資料會讓 Summarizer 對著現實中不存在的理想輸入調 prompt。

| 執行紀錄 | 內容 | `outcome` |
|---|---|---|
| `talk_only` | 只有文字,沒有工具 | (沒有 tool_result) |
| `single_tool` | 一次工具,成功 | `succeeded` |
| `multi_tool` | 多輪工具,全部成功 | `succeeded` ×2 |
| `tool_failure` | 指令跑了但 exit 非 0 | `failed` |
| `sandbox_denied` | 被沙箱擋下,**指令從未執行** | `denied` |
| `interrupted` | 中途被喊停 | `succeeded` + `interrupted` |

裡面的本機絕對路徑打過碼(`<workspace>` / `<home>` / `<project>`),
其餘一律保留原樣 —— 真的 token 用量、真的花費、真的耗時、真的工具輸出。
重新錄製或重新打碼:

```bash
python tools/record_run.py          # 會呼叫真的 Claude,約 $0.6
python tools/redact_fixtures.py     # 打碼,round-trip 無損才寫入
```

---

## R2 的洞:已經在核心補起來了

**這一段留著是為了記錄來龍去脈,問題本身已經解決,不需要任何人再做什麼。**

原本 `is_direct_evidence()` 只看有沒有 `tool_result`,不看那個工具有沒有真的
執行過。被沙箱擋下、或被人中止的工具呼叫都會產生 `ToolResultBlock`,照原樣送出去
就取得「直接證據」資格,Summarizer 於是被允許把「`rm` 不在白名單內」當成關於題目
的 Fact。

當時我在 bridge 這邊自己繞過去(把沒執行的降級成 `role: system` + `kind: message`),
並建議在核心開一個 optional 的 `outcome` 欄位。**那個建議被採納了**,現在:

```yaml
# spec/openapi.yaml — AgentRunEvent
outcome:
  enum: [succeeded, failed, denied, interrupted, unknown]
```

```python
# memory_core/summarizer.py — R2
return any(
    event.get("kind") == "tool_result"
    and event.get("outcome", "unknown") in {"succeeded", "failed"}
    for event in events
)
```

我那個 workaround 已經移除。現在的做法是**照實送出 `tool_result`,把執行狀態誠實
標在 `outcome` 上**,由核心決定它算不算證據 —— 比藏起來乾淨,而且換別的 Agent
Bridge 實作或換 provider 都受同一條規則保護。

一個實作細節值得知道:**沒有 `PostToolUse` hook 紀錄的 `tool_result`,我送
`unknown`**。沒有 hook 就無法證明工具真的跑過,寧可少一筆 Fact,也不要拿來歷不明的
輸出去支撐它。

---

## 為了合約留在本地的東西

`AgentRunEvent` 是 `additionalProperties: false`,所以這些只留在
`NormalizedRun.run_meta`:

| 留在本地的 | 本來想幹嘛 | 現在怎麼辦 |
|---|---|---|
| `truncated` / `original_bytes` | 讓 journal 在內容被截斷時仍誠實 | 截斷標記直接寫在 `content` 裡:`…[truncated N bytes]…` |
| `input`(結構化工具參數) | Bash 以外的參數是 dict | 攤成 `content` 的可讀字串 |
| `tool_use_id` | 配對 tool_call / tool_result | 只靠事件順序 |
| `duration_ms` | 工具耗時 | 只留本地當 telemetry |
| 成本 / turns / stop_reason | `RunLogInput` 沒位置 | 只留本地 |

不擋事,列出來只是讓你知道我手上有什麼 —— 之後要開 optional 欄位可以直接拿。

---

## 模板:分工已定,但有一件事要裁

`templates/` 現在是 `origin/template` 那批(crypto / generic / pwn / rev / web),
我原本的兩份已經被取代,**沒有重複問題了**。

`tools/lint_templates.py` 也跟著改了。它原本**禁止** `WEB-SQLI` 這種帶題型前綴的
寫法,對現在的模板噴 114 個錯 —— 錯的是規則不是模板。
`spec/template.schema.json` 的 `^[A-Z0-9]+-[A-Z0-9-]+$` 強制要有前綴段,
起點文件 §6 與規劃書 §7.1 的範例也都寫 `WEB-SQLI`。合約贏,規則移除,
結構改成直接餵給 schema 驗。

### 要裁的事:§7.3.6 現在是 no-op

規劃書 §7.3.6 說重新分類時「相同 technique ID 可保留 tried state」。
`memory_core/templates.py` 有實作它。但實測:

```
5 份模板,133 個 guess ID,跨檔共用 0 個
```

全部帶題型前綴,所以**那條規則永遠不會觸發**,實作等於死程式碼。
要嘛改命名讓 ID 跨題型共用,要嘛在 Spec 裡承認它是 no-op ——
但不該默默留著看起來像有在運作。

`tests/test_bridge_templates.py::test_no_guess_id_is_shared_across_templates`
是一條絆線:哪天有人改了命名,它會紅,提醒回頭重新評估。

---

## 還沒有答案的

### 給 P2:`agent.stop` 放哪、誰寫誰刪

這是 M3 唯一還缺的東西。`loop.StopSignal` 接受任意路徑,預設檔名 `agent.stop`。

但根本問題是:規劃書 §16 的 `cli/`(`ctfmem note/capture/show/query/guess/
modify/template/agent run/stop`)在任何 branch 上都不存在。`core/human_cli/`
是一個 PowerShell note app,有 `--send-note` 與 `--send-command-result`,
沒有 `show` / `query` / `guess` / `modify` / `agent stop`。

### 給 P1:`tried` 狀態沒有任何人在更新

規劃書 §4.3 的 `tried` 目前**只有 `POST /mutate` 的 `mark_tried` 能改**,
那是人類 override 的路。自動迴圈裡沒有任何人呼叫它,所以 `only_untried: true`
會一直發同一批建議,**Guessing 層在 YOLO 裡永遠不前進**。

`HttpMemoryClient.mutate()` 已經實作,管道是通的
(`tests/test_memory_client.py::test_mark_tried_is_expressible` 驗過),
但誰該負責更新是 Spec 要裁的,我不會單方面讓 bridge 去寫它 —— 那等於 Agent
在改白板。

順帶:`mark_tried` 不在規劃書 §8.6 的 operation 清單裡(add / replace / remove /
promote / demote / move),spec 要補。

### 三個人都要看:注入的白板會繞一圈回來

決定 3(bridge 強制注入白板)+ §13.4(run log 全部回 ingest)的交互作用。
Agent 對白板的複述會變成它自己的敘述,再被 Summarizer 當成新的 Assumption。

實測產物(真 Claude,兩圈):

```
A001  "I'll look at the workspace to see what's here."
A002  "The whiteboard swept up my narration line ... instead of the actual finding"
```

A002 是 Agent 在評論白板抄錯了它的開場白。

注入的文字**本身**不會被原樣送回(events 裡沒有 `user/message`),但複述會。
Memory Core 分不出「Agent 發現了 X」跟「Agent 在重複我剛才印給它看的 X」。
§4.4 明令禁止 `Guessing → Fact`,但這條路沒有任何一層擋。

要擋得在 envelope 開新欄位,**那是動合約,所以列在這裡等 Spec**,我不自己加。
