# 記憶系統效果實測（2026-07-30）

對象：`ais3-ctf6` @ `f412fbb`（main）。全部數據由本目錄的 `results/` 佐證，`scripts/` 可重現。

## 要回答的問題

> 共享白板有沒有降低主腦（Claude）的 token 消耗？

## 一句話結論

**在單一 session 解單一題的情境下，沒有。記憶系統比純 Claude 貴，而且難度越高相對劣勢越大。**
但白板產出的內容品質是好的，成本劣勢主要來自三個可修的機制缺陷，不是白板本身沒用。

---

## 1. 端到端對照：Memory Core + Claude vs 純 Claude

兩個 arm 的 runner、workspace、工具授權、預算上限全部對齊，唯一差異是有沒有接記憶系統。
`solution/` 一律排除，workspace 每次重建。

| 題目 | 難度 | 純 Claude | 記憶系統 | 倍數 | 記憶系統解出？ |
| ---- | ---- | --------: | -------: | ---: | -------------- |
| `rev/SekaiCTF2022-matrix-lab-1` | easy | **$0.5192** | **$0.9399** | 1.8× | ✅ |
| `rev/SekaiCTF2026-untitled-encore` | medium | **$1.6688** | **$5.6923** | 3.4× | ❌ 差一步 |

easy 那題的記憶系統數字是**開了 flag-stop 之後**的。沒有 flag-stop 時是 **$2.6325**（見 §3）。

medium 那題的「差一步」不是修辭：白板的 summary 已經記下正確的 40-byte chart
（`000c01070a072305140d0b062a06010b...`，與對照組解出的逐字元相同），
turn 3 的最後一句是「Found a solving chart. Verifying against the binary:」，
下一個動作就是驗證，預算在那一刻用完。

**難度上升，相對劣勢從 1.8× 擴大到 3.4×** —— 與「白板應該在需要累積的題目上發揮價值」的預期相反。

## 2. 白板內容品質：好

20 題各自開獨立白板，餵各自的題目檔案，共 **297 筆記錄**（194 facts + 103 assumptions）。
逐筆檢視見 `results/board-quality-297-records.txt`。

| 指標 | 數值 |
| ---- | ---- |
| 逐字重複記錄 | **0** |
| 落在 `general` 萬用區 | 6（2%） |
| 無 tags 記錄 | 74（25%），集中在 5 塊板 |

抽取品質有三個正面證據：**接地不編造**（只給 `file` + `strings` 輸出的題目，15 筆記錄全部可回溯）、
**保留不確定**（「format string not yet known」「likely 4x4」）、
**fact/assumption 分層確實運作**（推測進 `vulnerability_hypotheses` 且措辭為 "may/potential"）。

已知缺陷：tags 是**整塊板全有或全無**（4 塊 100% 缺、1 塊 65%、其餘 0%），
代表 summarizer 每次呼叫決定要不要出 tags，而 Memory Core 兩種都照收不補。

## 3. 分類鷹架：對準確度貢獻 0

同樣 20 題、同樣的輸入位元組，只差有沒有 Memory Core 的鷹架。見 `results/classification-control.json`。

| Arm | 說明 | 命中 |
| --- | ---- | ---: |
| 純關鍵字 | 只比對 `classifier_hints`，不呼叫 LLM | 11/20 |
| **裸模型** | 同一個 550b，**無鷹架**，最小提示 | **19/20** |
| **Memory Core** | templates 完整描述 + hints + MC system prompt | **19/20** |

兩邊漏同一題（`crypto/SekaiCTF2022-diffecient`），其餘 19 題逐題答案完全一致。
而那份鷹架佔 classify 提示的 **76%**（62,760 / 82,232 chars），74 次呼叫每次重送。

> 注意：這只測了**分類**。抽取階段的鷹架（template 分區、epistemic rules）沒有對照組，
> 其價值很可能遠高於分類階段。

## 4. 三個機制缺陷（成本劣勢的來源）

### 4.1 yolo 迴圈沒有完成訊號

`LoopLimits` 只有 `max_turns`、`max_cost_usd`、`idle_turns_before_stop`，沒有「解出了就停」。

easy 那題**第 1 輪就解完**，迴圈照樣跑到撞成本上限。`idle_turns_before_stop` 救不了：
`agent-status.json` 記錄 `turns=3 productive_turns=3`，每輪都有小幅白板變動，idle 計數從未累積。
連 `--idle-turns 1` 都不會觸發。

加上 flag-stop 後：**$2.6325 → $0.9399，省 64%**，1 輪停止，sha256 比對通過。
這就是本次一併修正的缺陷。

### 4.2 成本上限會嚴重超支

檢查在該輪**結束後**才做，所以最後一輪必定溢出，且題目越難溢出越多：

| 題目 | 設定 | 實際 | 超支 |
| ---- | ---: | ---: | ---: |
| matrix-lab-1 | $2 | $2.63 | 32% |
| untitled-encore | $3 | $5.69 | **89%** |

medium 那題就是因此在終點前一步被砍。硬限制應該傳進 adapter 的 `max_budget_usd`，而非事後檢查。

### 4.3 summarizer 輸入從前面截斷，砍掉最有價值的尾巴

`memory_core/providers/ais3.py` 對送進 summarizer 的 payload 做 `[:20000]`，
保留**前面**兩萬字。而 agent 的結論永遠在 run log 的**結尾**。

實測兩題兩中，且都中在做最多事的那一輪：

| 題目 | 解題/關鍵輪 payload | 截斷 | 後果 |
| ---- | ---: | ---- | ---- |
| matrix-lab-1 | 25,667 chars | 是 | flag 在被丟掉的尾巴裡，白板要靠第 2、3 輪才拿到 |
| untitled-encore | 36,907 chars | 是 | 寫出 eBPF disassembler 的那一輪，尾巴流失 |

**做最多事的一輪必然最長，也必然被砍尾。** 這與 4.1 是同一個問題的兩面。

## 5. Provider 錯誤會污染白板

provider 失敗時，錯誤字串被當正常 run log 送去 ingest，而 `source.model == "<synthetic>"`
就寫在資料裡卻沒有人消費（`run_meta` 的 `is_error` 被 `envelope.py` 明確排除在線上契約外）。

實測三次，行為取決於錯誤訊息的**措辭**：

| 錯誤訊息 | summarizer 的反應 |
| -------- | ----------------- |
| `Not logged in · Please run /login` | ❌ 捏造 A001「這題需要先認證」寫進白板，署名 agent，附 evidence |
| `API Error: 529 Overloaded` | ✅ 正確識別，白板保持空的 |

更糟的是誤診：三輪連續 529 時，CLI 回報「連續 3 圈白板沒有變化，停止避免原地打轉」，
`Productive turns: 0` —— 操作者看到的是「agent 沒本事」，實際是 API 掛了。
每一輪還各燒掉一次真的 550b summarizer 呼叫。

證據：`results/boards/armb2-matrixlab/`、`results/boards/armb4-matrixlab/`。

## 6. 量測缺口：token 拆分被丟棄

`normalize.py` 的 `_build_run_meta` 會收 `usage`，但 `envelope.py` 明確把 `run_meta` 排除
（「只留在本地當 loop telemetry」），而 `runtime.py` 的 `AgentStatus` 只存 `total_cost_usd`。

所以對照組（`claude -p --output-format json`）有完整的 input / output / cache_creation /
cache_read 明細，**受測系統只有美元金額**。有 prompt caching 之後金額與 token 嚴重脫鉤
（實測對照組 `input_tokens=22` 但 `cache_read=325,049`），這使得嚴格的 token 比較目前做不到。

## 7. 方法論陷阱（給後續實驗）

**沙箱差異會偽造出記憶系統有效的結論。** 同一題、同一預算、兩次都解出，只差工具是否授權：

| 執行 | 工具 | 花費 | output tokens |
| ---- | ---- | ---: | ---: |
| 對照組（沙箱擋掉解譯器） | 手算 | $1.8607 | 39,342 |
| 對照組（授權 javap/python） | 跑程式 | $0.5192 | 5,895 |

**成本膨脹 3.6 倍、output token 6.7 倍，全部來自工具可用性。**
`agent_bridge/sandbox.py` 的 M0 預設是唯讀 recon 名單（無 `javap`、無 `Write`、無 `python`、
連 shell 管線都禁），比一般 Claude Code 嚴格得多，而 `agent_cli.py` 沒有放寬的參數。
本次實驗的記憶組是用 `ToolPolicy.extend()` 手動放寬到對等才跑的。

> `sandbox.py` 的 docstring 自己就把「M2 接真題目時必須另外決定隔離層」列為待辦。
> 這裡提供了它的成本數據。

## 8. 未回答的問題

本次全部是**單一 session 解單一題**。記憶系統的價值主張是**跨 session 攜帶知識**，
而那個情境**完全沒有數據**。要驗證需要兩階段設計：
第一階段限制預算讓 agent 做不完 → 第二階段開 fresh session 接手 →
比對「有白板」vs「從零重來」。設計見 `ablation-config.yaml`。

其他限制：每題 n=1；只測了 rev 類別的 easy 與 medium 兩題；
20 題裡只有 11 題有真 ground-truth flag（5 題只有 `dummy_flag`/`test_flag` 佔位符）。

## 目錄

| 路徑 | 內容 |
| ---- | ---- |
| `ablation-config.yaml` | 消融實驗設定：固定變因、自變數、應變數、9 個已知混淆 |
| `flag-stop-design.md` | flag-stop 的設計決定與被否決的方案 |
| `results/classification-control.json` | §3 的三組分類對照 |
| `results/board-quality-297-records.txt` | §2 的 297 筆逐筆檢視 |
| `results/runs/` | 每次執行的原始 JSON 與 `agent-status.json` |
| `results/boards/` | 每塊實驗白板的 `state.yaml` 與 `journal.jsonl` |
| `scripts/` | 重現用的 harness |
