# CTF 共享記憶系統 v2 — 設計規劃書

> 狀態：設計草案  
> 目標：大幅簡化第一版 CTF Player，把「記憶系統」從自主 Agent 架構中抽離，改成一個可被人類與外部 Agent 共用的獨立白板／記憶服務。  
> 核心原則：**Memory Core 不是 Agent framework；唯一的模型元件是 Summarizer。**

---

## 1. 背景與重構目標

第一版專案以 `state.yaml` Board、rawstore、digest hook、Claude Agent SDK 與 playbook 組合成自主解題 Agent。實驗顯示 Board 本身有價值，但 digest、rolling context、自動代理與記憶管理彼此耦合，使研究變因與實作責任不易拆分。

v2 改為：

- 核心只管理 CTF 題目的共享記憶。
- 人類與外部 Agent 都是 Memory Core 的 client。
- 外部 Agent 的執行、YOLO loop、session 管理不屬於 Memory Core。
- Human CLI/TUI 不直接寫 `state.yaml`。
- Summarizer 是核心中唯一使用 LLM 的元件。
- 記憶只保留三個概念層次：
  - **Fact**
  - **Assumption**
  - **Guessing**
- 原始輸入另保留 append-only journal，作為 evidence/source record；它不是第四層語意記憶。

---

## 2. 研究依據與取捨

### 2.1 CoALA：保留記憶內容分類概念，不採用完整 Agent cognition

CoALA（Sumers et al., 2023）將 language agent 的記憶區分為 working、episodic、semantic、procedural 等概念。本專案不實作完整認知架構，而只借用一個重要區分：

- 題目目前已知的 Fact / Assumption：challenge-specific declarative state。
- Template 中的 Guessing catalog：更接近 procedural prior／可嘗試方法集合。
- 原始 run log / human input：保留為 episode/evidence source，而非直接等同事實。

參考：
- https://arxiv.org/abs/2309.02427

### 2.2 A-MEM：採 atomic item 與 stable ID，捨棄自動圖結構

A-MEM（Xu et al., 2025）使用結構化 note、metadata、link 與 memory evolution。v2 僅採用最小必要元素：

- 每個 Fact / Assumption 是 atomic item。
- 每項有 stable ID。
- 可有 section / tags / evidence references。
- 新資訊能修改、取代或重新分類既有 item。

不採用：

- vector database
- embedding retrieval
- graph database
- autonomous global linking
- 大量自動重寫歷史 note

參考：
- https://arxiv.org/abs/2502.12110

### 2.3 Generative Agents：raw experience 先保存，再做整理

Generative Agents（Park et al., 2023）的 memory stream → reflection → planning 提供一個重要原則：高階摘要不應取代原始 experience。

因此：

```text
Human input / Agent run log / CLI command result
                    ↓
              append-only journal
                    ↓
                 Summarizer
                    ↓
          Fact / Assumption updates
```

參考：
- https://arxiv.org/abs/2304.03442

### 2.4 LongMemEval：knowledge update 與 abstention

LongMemEval（Wu et al., 2024）明確評估 knowledge update 與 abstention。v2 因此要求：

- 舊知識可以因新 evidence 被修正。
- 證據不足時維持 Assumption 或不新增。
- Summarizer 不得為了「填滿白板」而把推論升格成 Fact。

參考：
- https://arxiv.org/abs/2410.10813

### 2.5 CHAP：只借用 attribution、diff、override、evidence log

CHAP（Shahid et al., 2026）提出 human-agent shared workspace、append-only evidence、override、diff、handoff 等 protocol 元素。v2 不實作 CHAP，而只採：

- actor attribution
- append-only source record
- human override reason
- semantic diff
- 明確 revision

參考：
- https://arxiv.org/abs/2606.09751

---

## 3. 最終系統邊界

```text
                    ┌──────────────────────┐
                    │      Human CLI       │
                    │ note/capture/query   │
                    │ guess/modify/agent   │
                    └──────────┬───────────┘
                               │ HTTP API
                               ▼
┌─────────────────────────────────────────────────────┐
│                  Memory Core Service                │
│                                                     │
│ Input → Journal → Summarizer → Proposal → Commit   │
│                           │               │         │
│                           ▼               ▼         │
│                      Templates        state.yaml    │
│                                          │          │
│                         query / guess / mutate       │
└───────────────────────────┬─────────────────────────┘
                            ▲
                            │ HTTP API
                            │
┌───────────────────────────┴─────────────────────────┐
│                  External Agent Bridge              │
│ Claude adapter / future adapters                    │
│ normal / YOLO                                       │
│ same-session / new-session                          │
│ memory skill                                        │
│ normalized run log → ingest                         │
└─────────────────────────────────────────────────────┘
```

### Memory Core 不負責

- Claude Agent SDK orchestration
- YOLO loop
- agent session lifecycle
- CLI/TUI UI state
- terminal PTY recording
- CTF exploitation sandbox
- 自動工具執行
- 多 Agent 協調

---

## 4. 三層記憶的嚴格語意

### 4.1 Fact

**對目前 challenge 已由直接證據或明確人工 override 確認的 atomic statement。**

適合成為 Fact 的來源：

- nmap / checksec / file / curl 等工具輸出直接可見結果
- challenge file 中可直接確認的內容
- exploit 成功後可重現的結果
- 人類以 override 明確聲明並提供 reason

外部 Agent 的自然語言推論本身不能自動成為 Fact。

範例：

```yaml
fact:
  services:
    - id: F001
      text: "TCP/80 is open and serves nginx."
      evidence: [I-31f4e9c2]
      source_actor: agent
```

### 4.2 Assumption

**與目前 challenge 直接相關、合理且可測試，但尚未被充分證明的 atomic proposition。**

可能來源：

- Agent 推論
- 人類的非 override 推測
- Summarizer 從多個 observation 得出的待驗證 attack path

範例：

```yaml
assumption:
  vulnerabilities:
    - id: A004
      text: "The login flow may be vulnerable to account-binding confusion."
      evidence: [I-f8d4a130]
```

### 4.3 Guessing

**由題型 template 提供的 curated technique catalog；不是 Summarizer 自由生成的記憶。**

例如 Web template 中可列：

- SQL injection
- SSRF
- SSTI
- auth confusion
- request smuggling
- deserialization

`state.yaml` 不必複製完整 catalog，只保存 template identity 與題目內的 tried/state：

```yaml
guessing:
  catalog: web@1
  state:
    WEB-SQLI:
      tried: true
    WEB-SSRF:
      tried: false
```

完整 technique metadata 由 template registry resolve。

### 4.4 允許的演化方向

```text
Guessing technique
      │ 被選中並形成具體題目假說
      ▼
Assumption
      │ 經直接 evidence 驗證
      ▼
Fact
```

但禁止：

```text
Guessing → 因模型覺得合理 → Fact
```

---

## 5. 持久化資料模型

每個 challenge 建議最小只有：

```text
runs/<challenge-id>/
├── state.yaml
└── journal.jsonl
```

另外全域 read-only：

```text
templates/
├── generic.yaml
├── web.yaml
├── pwn.yaml
├── rev.yaml
├── crypto.yaml
├── forensics.yaml
└── misc.yaml
```

### 5.1 `state.yaml`

建議：

```yaml
schema_version: 2
revision: 17

challenge:
  id: silver-bullet
  title: Silver Bullet
  category: pwn
  template: pwn
  template_version: 1
  summary: >
    32-bit ELF challenge. Current work focuses on ...

fact:
  binary:
    - id: F001
      text: "The binary is ELF i386."
      evidence: [I-83b70a9a]
      source_actor: agent
      created_at: "2026-07-25T..."
      updated_at: "2026-07-25T..."

  progress: []

assumption:
  exploit_path:
    - id: A001
      text: "power_up() may enable a memory-corruption primitive."
      evidence: [I-42aa8e92]
      created_at: "..."
      updated_at: "..."

  next_checks: []

guessing:
  catalog: pwn@1
  state:
    PWN-FMTSTR:
      tried: true
    PWN-RETLIBC:
      tried: false

meta:
  last_input_id: I-42aa8e92
  last_updated_at: "..."
```

### 5.2 `journal.jsonl`

append-only，保存輸入與 mutation source record：

```json
{"id":"I-83b70a9a","type":"input","actor":"human","input_type":"note","content":"..."}
{"id":"I-42aa8e92","type":"input","actor":"agent","input_type":"run_log","content":{...}}
{"id":"M-c420e4e1","type":"mutation","actor":"human","reason":"Verified in GDB","operations":[...]}
```

注意：

- journal 是 source/evidence record，不是 semantic memory layer。
- 不需要 event-sourcing framework。
- 不需要從 journal 每次重建全部 state。
- 研究時可用 journal replay 比較不同 Summarizer/template。

---

## 6. Summarizer

### 6.1 唯一 LLM 元件

Memory Core 中只有 Summarizer 使用 LLM。

責任：

1. 初次 challenge input 時協助選 template。
2. 將新 input + 當前 memory + template schema 整理成 `SummaryProposal`。
3. 將內容分類為：
   - 新 Fact
   - 新 Assumption
   - 更新／修正既有項目
   - 無需保存的內容
4. 更新 challenge summary。
5. 自然語言 query 時，僅依 whiteboard 資訊回答。

不負責：

- 選擇下一個 exploit tool
- 自主解題
- 執行 shell command
- 自主 YOLO loop
- 修改 Guessing catalog

### 6.2 Epistemic policy

Summarizer 必須遵守：

- tool result 中直接可驗證內容 → 可提案 Fact
- assistant/agent 的推測文字 → Assumption
- human 普通 note 的推測 → Assumption
- human explicit override → 可由 mutation API 直接成為 Fact
- 證據不足 → Assumption 或不新增
- template guessing item → 不可自行升格
- promotion Assumption → Fact 必須有新 evidence 或 human override reason

### 6.3 AIS3 模型

目前 AIS3 AI Infra 公開文件列出的 API base URL：

```text
https://llm-api.zoolab.org/v1
```

Chat Completions endpoint：

```text
POST /v1/chat/completions
```

指定模型：

```text
ais3/nemotron-3-ultra-550b
```

建議設定：

```bash
AIS3_BASE_URL=https://llm-api.zoolab.org/v1
AIS3_API_KEY=...
AIS3_SUMMARIZER_MODEL=ais3/nemotron-3-ultra-550b
```

設計上只依賴 OpenAI-compatible Chat Completions 基礎能力；不要假設特定 structured-output extension。模型輸出 JSON 後，以 Pydantic/schema 驗證，不合法時最多做有限 retry/repair。

來源：
- https://github.com/chunying/ais3-ai-infra

---

## 7. Template 系統

### 7.1 固定 top-level contract

所有題型 template 必須遵循同一 schema：

```yaml
id: web
version: 1
description: Web security challenge

classifier_hints:
  - HTTP
  - cookie
  - API
  - login

fact_sections:
  - target
  - endpoints
  - technologies
  - authentication
  - authorization
  - validated_behavior
  - progress
  - general

assumption_sections:
  - attack_surface
  - vulnerability_hypotheses
  - attack_paths
  - next_checks
  - general

guessing:
  - id: WEB-SQLI
    title: SQL Injection
    tags: [database, input]
```

所有 template 強制包含：

```text
fact.general
assumption.general
```

避免重新分類時資料無法映射。

### 7.2 分類

- challenge 第一次有足夠內容時可由 Summarizer 分類。
- 一旦選定，不應每次 ingest 自動重新分類。
- 之後只有 explicit reclassification 才更換 template。

### 7.3 Reclassification

重新分類時：

1. 保留 Fact / Assumption stable ID。
2. 不改變 epistemic level。
3. 只重新 map section/layout。
4. 無法映射的項目進 `general`。
5. 換 Guessing catalog。
6. 相同 technique ID 可保留 tried state。
7. 回傳 semantic diff。

核心原則：

> **換題型／換排版，不等於重新判斷真實性。**

---

## 8. API 合約

### 8.1 建立 challenge

```http
POST /v1/challenges
```

輸入：

```json
{
  "id": "silver-bullet",
  "title": "Silver Bullet",
  "initial_input": "..."
}
```

### 8.2 Ingest

```http
POST /v1/challenges/{challenge_id}/ingest
```

統一 vendor-neutral envelope。

Human note：

```json
{
  "source": {
    "type": "human",
    "actor": "ctshen"
  },
  "input": {
    "type": "note",
    "content": "I manually tested ..."
  }
}
```

CLI result：

```json
{
  "source": {
    "type": "human",
    "actor": "ctshen"
  },
  "input": {
    "type": "command_result",
    "command": ["nmap", "-sV", "target"],
    "exit_code": 0,
    "stdout": "...",
    "stderr": ""
  }
}
```

Agent run log：

```json
{
  "source": {
    "type": "agent",
    "provider": "anthropic",
    "model": "claude-...",
    "run_id": "run-123",
    "session_id": "session-abc"
  },
  "input": {
    "type": "run_log",
    "events": [
      {
        "seq": 1,
        "role": "assistant",
        "kind": "message",
        "content": "..."
      },
      {
        "seq": 2,
        "role": "assistant",
        "kind": "tool_call",
        "name": "Bash",
        "content": "checksec ./chall"
      },
      {
        "seq": 3,
        "role": "tool",
        "kind": "tool_result",
        "content": "NX enabled ..."
      }
    ]
  }
}
```

Claude-specific資料由 Agent Bridge normalize，Memory Core 不接受 Claude SDK object 作為 domain model。

### 8.3 讀取 memory

```http
GET /v1/challenges/{challenge_id}/memory
```

可支援：

```text
?layer=fact
?layer=assumption
?section=authentication
```

### 8.4 自然語言 Query

```http
POST /v1/challenges/{challenge_id}/query
```

```json
{
  "question": "目前登入機制已確認了什麼？",
  "layers": ["fact", "assumption"]
}
```

回應：

```json
{
  "answer": "...",
  "references": ["F014", "F018", "A009"],
  "revision": 42
}
```

### 8.5 Guess

```http
POST /v1/challenges/{challenge_id}/guess
```

```json
{
  "count": 3,
  "only_untried": true,
  "seed": 1337
}
```

`seed` 使 ablation 可重現。

### 8.6 Human mutation

```http
POST /v1/challenges/{challenge_id}/mutate
```

```json
{
  "expected_revision": 42,
  "actor": "human",
  "reason": "Verified manually in GDB",
  "operations": [
    {
      "op": "promote",
      "id": "A017",
      "to": "fact"
    }
  ]
}
```

只提供受控 operation：

- add
- replace
- remove
- promote
- demote
- move

不提供任意 YAML patch。

### 8.7 Template correction

```http
POST /v1/challenges/{challenge_id}/template
```

```json
{
  "template": "pwn",
  "expected_revision": 42,
  "actor": "human",
  "reason": "This is a binary exploitation task."
}
```

---

## 9. Diff

每次成功修改 memory 都回傳 semantic diff：

```json
{
  "revision_before": 16,
  "revision_after": 17,
  "summary": "The new scan confirmed an HTTP service.",
  "diff": {
    "fact_added": ["F014"],
    "fact_updated": [],
    "fact_removed": [],
    "assumption_added": ["A009"],
    "assumption_updated": [],
    "assumption_removed": [],
    "promoted": [],
    "demoted": [],
    "template_changed": null
  }
}
```

Human CLI 可 render：

```text
Memory rev 16 → 17

FACT
 + F014 TCP/80 is open

ASSUMPTION
 + A009 The service may be behind an nginx reverse proxy

TEMPLATE
   web@1
```

Diff 是 collaboration artifact，也是日後實驗記錄的一部分。

---

## 10. Race condition 與原子性

### 10.1 唯一 writer

**只有 Memory Core service 可以改 `state.yaml`。**

以下都禁止直接寫：

- Human CLI
- TUI
- Agent Bridge
- Claude skill

它們全部必須經由 API。

### 10.2 Revision + optimistic concurrency

```yaml
revision: 42
```

mutation 使用：

```json
{"expected_revision": 42}
```

若已成 rev 43：

```http
409 Conflict
```

client reload 後重試。

### 10.3 單服務 process 的 commit critical section

MVP 明確限制 Memory Core 使用單一 writer process。每個 challenge 在 commit 時需要短時間 critical section：

```text
lock
→ check current revision
→ validate proposal/mutation
→ write temp state
→ fsync as appropriate
→ atomic replace
→ increment revision
→ unlock
```

不要在 LLM 呼叫期間持有 lock。

### 10.4 Summarizer concurrency

```text
append input to journal
       ↓
read snapshot revision R
       ↓
Summarizer call (no lock)
       ↓
proposal(base_revision=R)
       ↓
short commit lock
       ↓
if revision == R:
    commit
else:
    re-read and re-summarize with bounded retry
```

這使 YOLO 執行期間 Human modify 不必等一整個 LLM request。

MVP 不支援：

- 多 worker Memory Core
- 分散式 lock
- network filesystem multi-writer
- external DB transaction

---

## 11. Human UX：CLI-first，TUI 後加

正式 automation contract 以 CLI 為主：

```bash
ctfmem init ./challenge
ctfmem note "..."
ctfmem capture -- nmap -sC -sV target
ctfmem show
ctfmem query "目前已確認哪些服務？"
ctfmem guess --count 3 --seed 42
ctfmem modify promote A014 --reason "confirmed manually"
ctfmem template set web
ctfmem agent run --mode normal "繼續分析"
ctfmem agent run --mode yolo --session same "把題目解完"
ctfmem agent stop
```

所有查詢類指令應有 machine-readable JSON mode。

未來 TUI：

```bash
ctfmem tui
```

定位為 YOLO 監看／人工介入 cockpit，不包含 domain logic。

---

## 12. CLI capture

第一版只做 command capture，不做完整 PTY recording：

```bash
ctfmem capture -- nmap -sV target
```

行為：

- command 正常執行
- stdout/stderr 同時顯示給使用者
- 執行結束後將：
  - argv
  - exit code
  - stdout
  - stderr
  送到 `/ingest`

後續才能追加：

- terminal session ID
- stdin/stdout/stderr event stream
- PTY recording
- background batching/summarization

未來 recorder 應以「command/event segment」批次 ingest，而不是每一行 terminal output 建一筆 memory item。

---

## 13. External Agent Bridge

Agent Bridge 使用 vendor-neutral adapter contract：

```python
class AgentAdapter(Protocol):
    async def start(...): ...
    async def continue_run(...): ...
    async def cancel(...): ...
```

第一版提供 Claude adapter。

外部 Agent 有 memory skill：

- `memory_show`
- `memory_query`
- `memory_guess`

預設：

1. 收到人類 prompt 後先 query memory。
2. Agent 覺得需要舊資訊時再 query。
3. 卡住時可 request guess。
4. Agent 不直接修改 semantic memory；其 run log 回到 `/ingest` 由 Summarizer 整理。

---

## 14. Normal / YOLO

### Normal

```text
Human prompt
→ query memory
→ Agent one run/turn
→ ingest run log
→ summarize
→ diff
→ stop
```

### YOLO

```text
Human prompt
→ query memory
→ Agent loop N
→ ingest
→ summarize
→ diff
→ query updated memory
→ Agent loop N+1
→ ...
```

YOLO 是 Agent Bridge orchestration，不屬於 Memory Core。

### Interrupt

```bash
ctfmem agent stop
```

至少保證：

- 不啟動下一 loop。
- provider 支援 cancel 時嘗試取消當前 run。
- 已完成的 log 仍盡量 ingest + summarize。

Human 可在 loop 間使用 `modify` 或 `note` 影響下一輪。

---

## 15. Same-session / New-session

### Same-session

外部 Agent 每輪延續同一 session。

```text
Agent conversation context
+
central memory
```

### New-session

每輪新建 Agent session。

```text
current prompt
+
central memory
```

這是一個重要 ablation：

- same vs new
- 衡量 central memory 能否承擔跨 run 記憶

Memory Core 不需要知道 provider session 的具體實作，只保留 `run_id/session_id/provider` metadata。

---

## 16. 三人分工

### Person 1 — Memory Core

負責：

```text
memory_core/
spec/
docs/memory-core-v2.md
```

內容：

- data model
- journal
- state store
- revision/concurrency
- Summarizer
- AIS3 provider
- template loader/classifier/reclassifier
- ingest
- memory read
- query
- guess
- mutate
- semantic diff
- OpenAPI contract
- unit/integration tests

不負責：

- Human CLI UX
- TUI
- Claude orchestration
- YOLO
- 真實各類 CTF technique catalog

### Person 2 — Human Interface

負責：

```text
cli/
tui/   # optional second phase
```

內容：

- API client
- init
- note
- capture
- show
- query
- guess
- modify
- template set
- diff renderer
- agent run/stop frontend
- JSON output
- 後續 TUI cockpit

禁止直接寫 `state.yaml`。

### Person 3 — Agent Bridge + CTF Templates

負責：

```text
agent_bridge/
templates/
```

Agent：

- Claude adapter
- provider-neutral event normalization
- memory skill
- normal loop
- YOLO loop
- same/new session
- cancel
- run-log ingest

Template：

- generic
- web
- pwn
- rev
- crypto
- forensics
- misc
- curated guessing catalog

禁止直接寫 `state.yaml`。

---

## 17. 三人共同 freeze 的 Contract

實作平行化前，三人共同確認：

```text
spec/
├── openapi.yaml
├── template.schema.json
├── memory.schema.json
└── protocol.md
```

至少 freeze 下列 domain types：

- `IngestEnvelope`
- `InputSource`
- `AgentRunEvent`
- `MemorySnapshot`
- `FactItem`
- `AssumptionItem`
- `GuessingState`
- `MemoryMutation`
- `MemoryDiff`
- `TemplateDefinition`
- `QueryRequest/Response`
- `GuessRequest/Response`

可以擴充 optional 欄位，但不得任意改變既有欄位語意。

---

## 18. 建議 Repo Layout

```text
ctf-memory/
├── docs/
│   └── memory-core-v2.md
├── spec/
│   ├── openapi.yaml
│   ├── memory.schema.json
│   ├── template.schema.json
│   └── protocol.md
├── memory_core/
│   ├── api.py
│   ├── models.py
│   ├── service.py
│   ├── store.py
│   ├── journal.py
│   ├── summarizer.py
│   ├── diff.py
│   ├── templates.py
│   └── providers/
│       └── ais3.py
├── cli/
├── agent_bridge/
├── templates/
└── tests/
```

現有第一版程式可暫留在 branch 中供參考；v2 不必強迫兼容其 Board schema 或 Agent loop。

---

## 19. Ablation 設計

新架構應讓以下實驗變因可獨立切換。

### 19.1 Session

- same-session
- new-session

### 19.2 Memory layer

- no memory
- fact only
- fact + assumption
- fact + assumption + guessing

### 19.3 Template

- generic
- category-specific

### 19.4 Guessing

- disabled
- seeded random guessing

### 19.5 Human intervention

- no intervention
- human note only
- human mutation/override

### 19.6 Summarizer

journal replay 允許固定 input 比較：

- 不同 prompt
- 不同 model
- 不同 classification policy

---

## 20. MVP 驗收標準

### Memory Core

- 能建立 challenge。
- 能保存 human note。
- 能保存 normalized agent run log。
- journal 保留原始 input。
- Summarizer 能產生 validated proposal。
- Fact / Assumption 遵守 epistemic policy。
- Guessing 只取自 template。
- 每次 commit revision +1。
- stale mutation 回 409。
- atomic state write。
- ingest 與 human mutation 不 lost update。
- 每次更新回傳 semantic diff。
- template 能 explicit reclassify。
- query 回答可 reference memory IDs。
- guess 支援 `seed`。
- 核心測試不需要真實 AIS3 API；live test 為 opt-in。

### 分工 Contract

- Person 2 可以只看 OpenAPI 就完成 CLI client。
- Person 3 可以只看 OpenAPI + template schema 就完成 Claude bridge 與 templates。
- Person 1 不需要等待另外兩人的實作即可完成 core mock integration tests。

---

## 21. 明確非目標

MVP 不加入：

- vector DB
- embeddings
- graph DB
- SQLite/Redis（除非後續實驗證明 YAML 已成為瓶頸）
- event-sourcing framework
- message broker
- multi-agent coordinator
- autonomous memory rewriting
- realtime collaborative editing
- multi-process Memory Core
- complex RBAC
- cryptographic signatures
- web GUI
- PTY terminal recorder
- background terminal daemon

---

## 22. 從第一版到 v2 的概念對照

| 第一版 | v2 |
|---|---|
| Board `state.yaml` | 繼續存在，但改成三層 semantic whiteboard |
| `facts` | `fact` |
| `hypotheses` | `assumption` |
| playbook | 部分轉為 template guessing catalog；Agent skill 另放 bridge |
| rawstore | 簡化為 `journal.jsonl` source record |
| digest | 移除 |
| rolling context | 移除 |
| hooks | Memory Core 不使用 |
| Opus commander | 移至 External Agent Bridge |
| resume Board | 改成 central memory API |
| 人類直接 edit YAML | 改為 mutation API；避免 race |
| jailed Agent orchestration | 不屬於 Memory Core |

---

## 23. 實作順序

### Phase 0 — Contract Freeze

三人先完成：

1. memory schema
2. template schema
3. OpenAPI
4. protocol
5. revision semantics
6. normalized run-log event schema

### Phase 1 — Memory Core

- YAML store
- journal
- template registry
- mock Summarizer
- AIS3 Summarizer provider
- ingest
- diff
- read/query/guess/mutate/template
- tests

### Phase 2 — Human CLI

- note
- capture
- show/query/guess/modify
- JSON output

### Phase 3 — Agent Bridge

- Claude adapter
- memory skill
- normal mode
- same/new session

### Phase 4 — YOLO

- iterative loop
- stop/cancel
- human intervention between loops

### Phase 5 — Templates/TUI/Experiments

- 完整 CTF templates
- TUI cockpit
- ablation harness

---

## 24. 核心設計判斷

v2 最重要的不是建立「更智慧的 Agent 記憶」，而是讓以下四件事彼此解耦：

1. **Observation/source record**：人類或 Agent 到底做了什麼。
2. **Epistemic memory**：哪些是 Fact，哪些仍只是 Assumption。
3. **Procedural prior**：題型 template 允許抽取哪些 Guessing 技術。
4. **Execution context**：外部 Agent 是否 same-session、是否 YOLO。

這使 central memory 可以被獨立測量、替換與做消融，並讓人類在 Agent 執行期間透過 revision-safe mutation 改變共享白板，而不必把 Memory Core 本身做成複雜 Agent framework。
