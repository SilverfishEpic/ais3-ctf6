# ctfmem：「拿到 flag 就停」的設計決定

對象：`C:\Users\Ronny\Desktop\AIS3-Project\ais3-ctf6` @ `f412fbb`
狀態：本文件不修改 repo 任何檔案。今天能跑的東西在 `ctfmem_flagstop.py`（同目錄），
長期形狀在 §7「團隊必須決定的事」。

---

## 0. 一句話結論

**勝出的是 turn-signal 家族（harness 自己判斷、不進 Memory Core），但必須重塑成
「只讀本輪 tool output 的 flag sighting，再減掉 t=0 的 workspace baseline」。**
board-state（`ChallengeMeta.solution`）是正確的長期形狀，但今天不能上；
convergence 是有用的備援煞車，但預設必須關掉。

今天就能跑：

```
cd C:\Users\Ronny\Desktop\AIS3-Project\ais3-ctf6
.venv\Scripts\python.exe <scratchpad>\ctfmem_flagstop.py --flag-prefix SEKAI \
    --challenge <id> agent run "<prompt>" --mode yolo --workspace <challenge dir> \
    --max-turns 6 --max-cost-usd 3.0
```

---

## 1. 要解的問題（實測，不是假設）

- `rev/SekaiCTF2022-matrix-lab-1` 的 yolo run **第 1 圈就解掉了**
  （javap → 寫 `solve.py` → 跑 → 對真 JVM 驗證，印出 "Congratulations, you got the flag!"）。
- 迴圈照樣跑了第 2、3 圈，最後是撞到 `--max-cost-usd` 才停，總共 **$2.63**。
  對照組（純 `claude -p`，無記憶系統，同樣 tool grants）**$0.52**。
- 第 2 圈的最後一句是「The challenge is solved and closed - no work remains.」
  第 3 圈是「Nothing substantive remains; I won't re-run verification a third time.」
- 原因：`agent_bridge/loop.py` 的 `LoopLimits` 只有 `max_turns`、`max_cost_usd`、
  `idle_turns_before_stop`，**沒有任何完成訊號**；而 `idle_turns_before_stop` 沒有觸發，
  因為白板每圈都還在微幅變動（多一個 assumption、降級一個 fact），idle 計數一直被歸零。

誠實的最便宜對照：`--idle-turns 1` 會讓那一次 run 在第 2 圈結束時停下來，**零程式碼、零 spec、零 ADR**。
它省下的只有一圈，而且它分不出「解完了」與「卡住了」。任何比它複雜的方案，都必須用這一行當基準線來評價。

---

## 2. 為什麼是 turn-signal（重塑版）勝出

| 候選 | 判定 | 決定性理由 |
|---|---|---|
| **turn-signal**（harness 擁有的成功判準） | **採用（重塑）** | 唯一「不碰 `spec/`、不碰 domain model、不需要 ADR、今天就能包成 wrapper 跑」的方案。它把判斷放在 bridge，而 bridge 本來就已經是 `interrupted`、`cost_usd`、`changed_board` 的唯一權威 —— 完成訊號就是 loop telemetry。 |
| board-state（`ChallengeMeta.solution`） | **延後，不是否決** | 方向正確（可 grade 的產物、白板是唯一權威），但今天上不了：見 §5.6。它需要 ADR + `scripts/gen_spec.py` 重生 + 三個 `spec/` 檔案，而且**在修好 summarizer 的 20 000 字截斷之前，它在真正解題的那一圈是結構性瞎的**。 |
| convergence（資訊產出收斂） | **降級為 opt-in 備援** | 核心命題是對的（問「這圈有沒有產出新資訊」對每一題都有答案），但它**回答不了使用者的問題**——使用者要的是「拿到 flag 就停」，收斂只能說「不再產出」。而且它有三個會誤殺的結構性問題：見 §5.10。 |

重塑的部分（原 turn-signal 提案不能照抄的地方）：**tier 1 的 `--verify-command "python solve.py"` 被否決**，
因為那支程式是 agent 自己寫的，exit code 與 stdout 完全由 agent 控制，而 baseline probe 對它零覆蓋
（t=0 時 `solve.py` 還不存在，所以 probe 一定通過，等於沒檢查）。
取而代之的是**不執行任何子程序**的 flag sighting。

---

## 3. 勝出設計：四層防線

判斷的乾草堆**只有一個**：本輪 `NormalizedRun.events` 裡 `kind == "tool_result"`
且 `outcome ∈ {succeeded, failed}` 的事件內容。

### 第 1 層：頻道隔離（主要防線）
- **永遠不讀白板。** summarizer 曾經把一句「描述 flag 格式」的話變成白板上的
  `SEKAI{''; Flag suffix: ''}`；67 份 `runs/*/state.yaml` 掃出的 15 個 flag 形狀 token，
  有 7 個在 `fact/target`，是 turn 1 從 README/solution 折進來的、根本沒人解出的 run。
  任何讀白板的 oracle 都會在那裡誤觸。
- **永遠不讀 assistant 的話。** 「我拿到 flag 了」是自我回報，repo 自己的 R2/R3 就已經拒絕這類證據。
- **排除 memory MCP 工具的輸出**（`mcp__memory__memory_show` 等），因為那個 tool_result 的內容**就是白板**。

### 第 2 層：t=0 workspace baseline（對付 committed flag）
開跑前掃一次 workspace（含二進位檔，以 latin-1 解碼等同 `strings`），把所有 flag 形狀 token 記下來當抑制名單。
實測（本次以 `--flag-prefix SEKAI` 掃 `test-challenge-set/*/*/challenge/`）：

```
12 / 20 題在 challenge/ 底下就有一個「形狀合理」的真 ground-truth flag
   diffecient, cryptography-2, squares-vs-cubes, iihash, bfs, cosmic-ray,
   nolibc, vkfs, echo-chamber, sekai-licensing, sekai-game-start, migurimental
另有 leakless-note 的 README 帶 SEKAI{[a-z]+}（格式 regex）
```

也就是說：**只做「工具真的印出過這個字串」的 containment，會在超過一半的題目上於第 1 圈誤報成功。**
baseline 把這 12 題全部抑制掉；format regex 另外被第 3 層擋掉。

### 第 3 層：合理性過濾（對付 placeholder 與 prose）
body 為空 / 在 placeholder 名單（`dummy_flag`、`test_flag`…）/ 少於 4 字 /
含空白、引號或分號（＝那是散文）/ 全由 `.…*?xX_-` 組成 / 看起來是 format string（`%s`）/
看起來是 regex（含 `[` 與 `]`，或含反斜線）→ 一律丟棄。

實測全部被殺：`SEKAI{''; Flag suffix: ''}`、`SEKAI{}`、`SekaiCTF{...}`、`sum_{i=0}`、
`SEKAI{test_flag}`、`SEKAI{dummy_flag}`、`SEKAI{[a-z_]+}`、`SEKAI{%s}`、
`SEKAI{") && substring3.equals("}`、`SEKAI{____}`；
形狀合理的真 flag 會通過；原始答案不保留在文件中。

### 第 4 層：可選的 ground-truth 確認（給 eval 用）
`--expect-flag-file PATH`：wrapper 自己讀檔，**只在記憶體裡留 SHA-256**，
不寫進任何 argv、不落磁碟。這一點是刻意的：agent 的白名單裡有 `cat`，
`cat /proc/<ppid>/cmdline` 就能把命令列上的答案讀走。
此模式下只有 hash 命中才算 `SOLVED`；命中但該 token 在 baseline 裡 → 標成 **`LEAKED`**
（agent 是去讀答案，不是解出來的），一樣停，但**不能算解題成功**。

### 停的機制（wrapper 版）
`run_yolo` 在每次迭代的**開頭**才檢查 `self.stop.raised`（`loop.py:181`）。
所以在一圈結束後把該旗標升起，下一次迭代會在呼叫 adapter 之前 break：
**多花 0 圈、0 元**，而且 loop 自己的 `finally: self.stop.restore()` 照常執行。
之後再改寫 `outcome.stopped_because`，讓 `agent-status.json` 與 CLI 輸出說的是 `flagstop/solved:...`
而不是 loop 泛用的「人喊停」。

### 停止分類（輸出一定講清楚）
`SOLVED` / `LEAKED` / `CLAIMED` / `CONVERGED` / `PROVIDER_ERROR` / `BUDGET` / `IDLE` /
`TURN_CAP` / `HUMAN_STOP` / `INTERRUPTED` / `OTHER`。
`CLAIMED` 與 `CONVERGED` 在輸出上明講「UNVERIFIED，統計時算未解出」。

---

## 4. 從兩個亞軍吸收了什麼

**從 board-state：**
- **S3/S4 containment 規則**：flag 必須逐字出現在「真的執行過的工具輸出」裡；
  assistant 訊息、人類 note 一律不算。這是整個設計的骨幹。
- **「模型只能指，不能寫」**的倒轉思路 —— 在 wrapper 版變成「harness 只能看，不能問」。
- 長期形狀（`challenge.solution` 當可 grade 的產物）原封不動保留到 §7。

**從 turn-signal：**
- **baseline probe** 的概念，但一般化成「掃整個 workspace」而不是「跑一次 verify command」，
  因為後者對 agent 自己寫的程式零覆蓋。
- **plausibility filter** 與 placeholder 名單。
- **`solved=None` 的誠實原則**：自我宣告只能是 `claimed`，永遠不能算成功。
- 拒絕任何自然語言「聽起來完成了」的啟發式（`/solved|complete|no work remains/`
  會同時命中 "the challenge is **not** solved"）。單一、行首錨定的 sentinel token，而且預設關閉。

**從 convergence：**
- **`turn_capped` 與 `turn_failed` 必須分開**：`error_max_turns`、`error_max_budget_usd`
  也會設 `is_error=True`。把它們當 provider 失敗，會在預設設定下殺掉最長、最有產出的那些圈，
  而且標成 `provider_error`。wrapper 裡已經用 `CAPPED_SUBTYPES` 分開了。
- **沒有任何 tool_result 的圈不能算收斂**（summarizer 的 R3/R4 會強制把無直接證據的提案降級，
  所以 `fact_added == []` 是構造上必然，`converged` 對這種圈是恆真式）。
- **整圈工具全部 failed/denied 的情況不算收斂**（那是被外部擋住，不是做完了）。
- 穩定的 `stop_class` enum（`stopped_because` 是自由格式中文字串，沒有東西解析得了）。
- ablation 控制臂必須存在（`--flagstop-off`）。

---

## 5. 被否決的想法，以及否決理由

| # | 想法 | 否決理由 |
|---|---|---|
| 1 | 對白板 / `state.yaml` 跑 `SEKAI\{...\}` regex | 67 份白板掃出 15 個 flag 形狀 token，7 個在 `fact/target`（turn 1 從 README 折進來，題目根本沒解）；還有 `sum_{i=0}`、`SEKAI{}`、`SEKAI{test_flag}`。**第 1 圈就會誤報成功。** |
| 2 | 用 `tags=['solved']` 標記一個 fact | tag 命名空間已經被污染：`tags=['flag','source']`、`tags=['flag-format']`、未解出的 run 上有 `tags=['solvable']`。而且這是用「慣例」而非「結構」編碼終止狀態，正是 `SummaryProposal` docstring 自己拒絕的模式。 |
| 3 | 在 5 個 template 加一個 `solution` section | `crypto/recovered_values` 與 `generic/recovered_data` 在 67 份白板裡**一個項目都沒有**。模型被告知卻從不使用的 section 不是頻道。外加 5 次 template 版本升版。 |
| 4 | `MemorySnapshot` 加一個 top-level `solution` 區塊 | 真的變成第四個白板概念，直接撞 `spec/protocol.md §2`「Three memory concepts, and only three」，ADR 難寫十倍。 |
| 5 | 在 `IngestResponse` 加欄位 | 那是 edge 不是 level（resume 的 run 看不到上一個 session 的宣告）；而且 `additionalProperties: false`，等於改 wire contract，卻沒比讀 level 便宜。 |
| 6 | `ChallengeMeta.solution`（board-state 提案本體） | **延後，非永久否決。** 阻擋原因有三：(a) `memory_core/providers/ais3.py:141` 把 source record 從**開頭**切到 20 000 字，實測換算約每圈 14 KB tool output 就會超過 —— 而解題那一圈必然是最長的一圈，所以模型在最關鍵的一圈看不到 flag，反而看得到早期便宜的 README 格式字串；(b) 提案的 S5「第一個宣告永久勝出、只有人類能撤回」在無人的 yolo loop 裡不可行；(c) 把宣告的 flag 注回白板（`inject.py` 的 `【已解出】`）是整個設計裡最高槓桿的污染向量。 |
| 7 | `--verify-command "python solve.py"`（turn-signal tier 1） | 那支程式是 **agent 自己寫的**；exit code 與 stdout 完全由 agent 控制。baseline probe 對它零覆蓋（t=0 檔案不存在 → probe 必過）。把它標成 `verified` 是把「自信地錯」升級成「靜默的假成功」，比原本的 $2.63 更糟。 |
| 8 | `--expect-flag <literal>` 放命令列 | `cat` 在 agent 白名單上，WSL 下 `cat /proc/<ppid>/cmdline` 就能讀走 ground truth，直接讓那 11 題的 eval 失效。改成 `--expect-flag-file` + 記憶體內 SHA-256。 |
| 9 | 自然語言「聽起來做完了」啟發式 | `/solved|complete|no work remains/` 會命中 "the challenge is **not** solved" 與 "I will now verify the solution"。 |
| 10 | convergence 預設開啟 | (a) 無工具的圈是恆真收斂（R3/R4 強制 `fact_added == []`），think-heavy 的 crypto 題兩圈必停；(b) `session=new` 臂沒有對話歷史，第 2 圈只能重下 `ls`/`file`/`cat` → N=0、白板飽和 → G=0，**這一臂會被結構性殺死**；(c) 「解完了」與「被校園網路擋住、每圈重跑同一個 exploit」會被壓成同一個 `converged` 標籤；(d) 觸發靈敏度與 memory policy（自變數）相關 → 對消融實驗是 confound，不是雜訊。故改成 opt-in。 |
| 11 | `turn_failed()` 直接看 `is_error` | `error_max_turns` / `error_max_budget_usd` 也會設 `is_error`。而 `registry.py` 把 loop 的 `--max-turns`（預設 10）直接餵給 adapter，rev/pwn 一圈用掉 10 個 SDK turn 是家常便飯 → 最有產出的圈會被判成 provider error 並在兩圈後結束 run。 |
| 12 | 直接改 repo（加 `LoopLimits.stop_when_solved` 等） | 這次的交付條件是不動 repo；而且真要改，第一步應該是 ADR，不是程式碼（`docs/memory-core-v2.md:95-97` 的規則就是「先寫 ADR 描述觀察到的問題」）。 |
| 13 | 只把 `--idle-turns` 調成 1 就當作解決 | 是最誠實的便宜基準線（那次 run 會在第 2 圈結束時停），但它分不出「解完」「卡住」「在想事情」，而且對長思考的題目會誤殺。**建議與本方案並用當對照，不當答案。** |

---

## 6. 這個東西做不到什麼

1. **不能證明 flag 是對的。** 一次 sighting 只證明「某個真的跑過的工具印出了這個形狀合理、且開跑前不在磁碟上的字串」。
   沒有 `--expect-flag-file` 時，`SOLVED` 應該讀成 `stopped_with_a_candidate`。
2. **不能在一圈中途停。** 最早的停點是「解出來的那一圈結束時」。省的是第 2..N 圈，解題那一圈照樣全額付費。
3. **看不到被截斷掉的 flag。** loop 拿到的 tool_result 已經是 head 2000 + tail 1000；
   整輪超過 60 000 字時的第二輪削減是**只留頭**。埋在超大 dump 中段的 flag 不可見（印在最後的通常還在）。
4. **看不到沒經過工具的解。** 純推理得出、或從截圖讀到並只在話裡講出來的 flag，一律看不到 → 退回原本的煞車。
5. **分不出「推導出來」與「run 中途自己造出來的誘餌」。** baseline 只涵蓋開跑前就存在的檔案。
   agent 自己 `echo` 一個 `SEKAI{i_guess_this}` 會被當成 sighting（形狀合理的話）。
   這是本方案最主要的假陽性殘留，只能靠 `--expect-flag-file` 或人看 evidence 行消除。
6. **不寫任何東西進 Memory Core。** `state.yaml` 依然沒有 flag 的概念，所以**這不會產生可 grade 的產物**；
   eval harness 只能讀 wrapper 寫的 sidecar JSON。要進 `state.yaml` 就是 §7 的合約變更。
7. **不修 cost 重複計算**（`LoopOutcome.total_cost_usd` 累加可能是 session 累計的 `total_cost_usd`），
   也不修 `registry.py` 把 `max_budget_usd` 設成等於 `max_cost_usd`（一圈可以合法花掉整個 run 的預算）。
8. **不處理 provider 硬死。** `ClaudeAdapter._run` 沒有 try/except，SDK 例外會穿過 `run_yolo`，
   `agent-status.json` 會永遠停在 `"running"`（`write_status` 在 loop 回來之後才執行）。這是既有 bug，本方案不觸碰也不掩蓋。
9. **會污染消融比較。** 這改變了停止規則。對照臂請用 `--flagstop-off`，
   或兩臂都開、並在結果表裡逐 run 記錄 `stop_class`。

---

## 7. 團隊必須決定的事

這一節全部涉及 `spec/`、domain model 或 Non-goals，**不能由一個人在 wrapper 裡決定**。

### 7.1 `ChallengeMeta.solution` 要不要落地（需要 ADR 0004）
- **為什麼值得**：目前「解出來了」這件事**從來沒有進入持久紀錄**。
  加了之後 `state.yaml → challenge.solution.flag` 才是 eval harness 可以直接跟
  `test-challenge-set/*/solution/` 對答案的產物；resume 的 run 也才知道不用重跑。
- **代價**：改 `memory_core/models.py` → 必跑 `python scripts/gen_spec.py` →
  `spec/openapi.yaml`、`spec/memory.schema.json` 變動，`spec/protocol.md` 要手改；
  `tests/test_spec_drift.py:24-27` 會擋到重生為止。`spec/template.schema.json` 不動。
- **必須同時做的三件事**（缺一則新欄位在真正解題的那一圈是空的）：
  1. 先修 `memory_core/providers/ais3.py:141` 的 `[:20000]`，改成頭尾都留（比照 `truncate.py`）。
  2. `MemoryDiff` 加 `solved: bool`，否則「只有解題宣告」的那一圈 diff 全空 →
     `changed_board = False` → idle 計數在最重要的一圈往上跳。這也是 spec 變更。
  3. 放棄或縮小 S5「第一個宣告永久勝出」：無人的 yolo loop 沒有人可以撤回。
- **明確不要做**：不要把宣告的 flag 經 `inject.py` 注回白板。

### 7.2 loop 要不要有正式的第四個煞車與穩定的 `stop_class`
`AgentStatus`（`agent_bridge/runtime.py:21-34`）是 client-owned 的本地 JSON、**不在 `spec/` 裡**，
加欄位是免費的。建議至少把 `stop_class` 加進去，並在 `_emit_outcome` 一起輸出 ——
`stopped_because` 是自由格式中文字串，下游沒有東西解析得了。

### 7.3 Non-goals 的判讀（我的看法，請 review）
`docs/memory-core-v2.md:87-97` 與 `spec/protocol.md §14` 都**沒有**列出「完成訊號」；
`loop.py:7` 明講「YOLO 屬於 bridge，不屬於 Memory Core」。
所以**bridge-local 的煞車不需要 ADR**；**Memory Core 的欄位需要**。
但 reviewer 一定會問一件事，ADR 必須正面回答：
`MemoryClient` protocol 依然排除 `mutate`，所以沒有新增 agent 寫入路徑；
可是 **agent 的 stdout 從此可以透過一個白板欄位決定 loop 何時終止**——那是一種新的權限，即使不是新的 endpoint。

### 7.4 `registry.py:78` 硬寫死的 `ToolPolicy()`
`ToolPolicy.extend()` 在正式程式碼裡**沒有任何呼叫者**。預設白名單沒有 `javap`、`java`、`python`，
也沒有 Write，所以「agent 寫 `solve.py` 再跑」在 committed 預設下是做不到的 ——
觀察到的那次 matrix-lab-1 run 沒有走這條路徑。
`ctfmem_loose.py` / `ctfmem_flagstop.py` 存在只是因為 CLI 沒有這條路。
請決定要不要加 `--tool-policy`，並在 ADR 裡寫清楚 yolo harness 實際跑在哪一組 policy 下。

### 7.5 消融實驗的政策
對照臂用 `--flagstop-off`，或兩臂都開；無論哪種，`stop_class` 必須逐 run 記錄成共變量。
不要讓這件事默默進到結果表。

### 7.6 既有 bug（與本設計正交，但會影響上面的結論）
- `ClaudeAdapter._run` 無例外處理 → run 崩掉時 `agent-status.json` 停在 `"running"`。
- `registry.py` 讓 `max_budget_usd == max_cost_usd`：一圈可合法花掉整個 loop 的預算。
- summarization 是 per-record 且不重試：解題那一圈遇到 529，flag 會永遠留在 `journal.jsonl` 裡、無法被宣告。

---

## 8. 建議的跑法

```bat
:: 一般用法（沒有 ground truth；9/20 題的 flag 在遠端服務的 FLAG env var）
.venv\Scripts\python.exe <scratchpad>\ctfmem_flagstop.py ^
    --flag-prefix SEKAI ^
    --flagstop-json runs\flagstop-<id>.json ^
    --challenge <id> agent run "<prompt>" --mode yolo ^
    --workspace test-challenge-set\rev\SekaiCTF2022-matrix-lab-1\challenge ^
    --max-turns 6 --max-cost-usd 3.0

:: eval 用（11 題有真答案）：只有 sha256 命中才算解出，並會標出「其實是去讀答案」
    ... --expect-flag-file test-challenge-set\rev\<x>\solution\flag.txt

:: 對照臂（行為與今天完全相同，只多印一行 verdict）
    ... --flagstop-off
```

`--stop-on-claim 2` 與 `--converged-turns 3` 是備援，**預設關閉**，理由見 §5.9、§5.10。
兩者都只會產生 `CLAIMED` / `CONVERGED`，統計時一律算**未解出**。

---

## 9. 檔案

- 設計（本文）：`<scratchpad>\flag-stop-design.md`
- 可執行 wrapper：`<scratchpad>\ctfmem_flagstop.py`
- 沿用的沙箱放寬：`<scratchpad>\ctfmem_loose.py`（`ctfmem_flagstop.py` 已內含同樣的 grant）
- 未來要改的地方（尚未改）：
  `agent_bridge\loop.py`（`LoopLimits` / `run_yolo` 第 191 行）、
  `agent_bridge\runtime.py`（`AgentStatus`）、
  `human_client\agent_cli.py`（第 64 行後的旗標、第 141-145 行的 `LoopLimits(...)`）、
  `memory_core\models.py` + `spec\`（僅在 §7.1 通過後）、
  `memory_core\providers\ais3.py:141`（截斷，§7.1 的前置條件）。
