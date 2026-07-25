# CTF 共享記憶系統 v2 — 三人共通設計起點

這份文件的用途不是再討論整體架構，而是讓三位開發者能在同一天開始平行工作，不因彼此尚未完成實作而互相阻塞。

---

## 0. 先同意一句話

> **Memory Core 是唯一 semantic-memory writer；Human UI 與 External Agent 都只是 API client。**

只要這句話不被破壞，後續 CLI、TUI、Claude、YOLO、模板與研究實驗都能獨立演進。

---

## 1. 第一個共同工作項：Freeze Domain Contract

三人先共同建立並 review：

```text
spec/
├── openapi.yaml
├── memory.schema.json
├── template.schema.json
└── protocol.md
```

在開始各自大量寫 code 前，先 freeze v0 contract。

### 必須共同定義的 type

```text
IngestEnvelope
InputSource
AgentRunEvent

MemorySnapshot
FactItem
AssumptionItem
GuessingState

MemoryMutation
MutationOperation
MemoryDiff

TemplateDefinition

QueryRequest
QueryResponse
GuessRequest
GuessResponse
```

---

## 2. 共同語意：Fact / Assumption / Guessing

### Fact

challenge-specific 且已由直接 evidence 或明確 human override 確認。

### Assumption

challenge-specific、合理、可測試，但尚未充分確認。

### Guessing

template-specific curated technique catalog。

**Guessing 不是模型自由生成的 brainstorm。**

共同規則：

```text
Guessing → concrete hypothesis → Assumption → evidence → Fact
```

Agent 的自然語言推論不能直接成為 Fact。

---

## 3. Source / Evidence Contract

所有外部 input 先 normalize 成：

```json
{
  "source": {
    "type": "human | agent",
    "actor": "...",
    "provider": "...",
    "model": "...",
    "run_id": "...",
    "session_id": "..."
  },
  "input": {
    "type": "note | command_result | run_log",
    "...": "..."
  }
}
```

Memory Core 必須先把 input 原樣保存至 append-only journal，再交給 Summarizer。

Claude SDK event 不得直接成為 Memory Core 的 domain model。

Person 3 負責：

```text
Claude event → AgentRunEvent / IngestEnvelope
```

Person 2 負責：

```text
human text / process stdout/stderr → IngestEnvelope
```

Person 1 只處理標準 envelope。

---

## 4. Revision Contract

每個 challenge：

```yaml
revision: 42
```

所有 explicit mutation/template correction：

```json
{
  "expected_revision": 42
}
```

不符合：

```http
409 Conflict
```

共同要求：

- client 不能直接 edit YAML。
- Memory Core 是唯一 writer。
- commit 使用短 critical section。
- state 使用 temp file + atomic replace。
- LLM request 期間不持有 state lock。
- MVP 只承諾單 Memory Core process。

---

## 5. Diff Contract

每次成功 ingest/mutation/reclassification 都回：

```json
{
  "revision_before": 41,
  "revision_after": 42,
  "summary": "...",
  "diff": {
    "fact_added": [],
    "fact_updated": [],
    "fact_removed": [],
    "assumption_added": [],
    "assumption_updated": [],
    "assumption_removed": [],
    "promoted": [],
    "demoted": [],
    "template_changed": null
  }
}
```

Person 1 產生 machine-readable diff。

Person 2 只 render。

Person 3 可把 diff 當作 loop telemetry，但不能自行修改 diff。

---

## 6. Template Contract

所有 template：

```yaml
id: web
version: 1

fact_sections:
  - ...
  - general

assumption_sections:
  - ...
  - general

guessing:
  - id: WEB-SQLI
    title: SQL Injection
    tags: [...]
```

共同 invariant：

- `fact.general` 必須存在。
- `assumption.general` 必須存在。
- Guess ID 在 template version 內穩定。
- reclassification 不改變 Fact/Assumption epistemic level。
- 無法映射的 item 放 `general`。

Person 1 負責 loader/schema/reclassification algorithm。

Person 3 負責真實 CTF template 內容。

---

## 7. 三人的最小 Mock Boundary

為避免等待：

### Person 1 提供

Mock server / fixture：

```text
POST /ingest
GET  /memory
POST /query
POST /guess
POST /mutate
POST /template
```

### Person 2 提供

CLI 只依賴 OpenAPI，可以先對 mock server 開發：

```bash
ctfmem note
ctfmem capture
ctfmem show
ctfmem query
ctfmem guess
ctfmem modify
```

### Person 3 提供

假 Agent adapter 可以先輸出固定 normalized run log：

```text
FakeAgentAdapter
→ IngestEnvelope
```

以及最小：

```text
templates/generic.yaml
templates/web.yaml
```

---

## 8. Branch / Integration 原則

建議：

```text
redesign/memory-core-v2     Person 1
redesign/human-client-v2    Person 2
redesign/agent-bridge-v2    Person 3
```

三人先以共同 contract commit 作為共同 base，再各自 branch。

若目前尚未建立共同 contract commit，先不要讓三個 branch 各自發明不同 schema。

---

## 9. Definition of Done：共同 Contract v0

三人開始獨立實作前，必須能回答「是」：

- [ ] Fact / Assumption / Guessing 的語意一致。
- [ ] IngestEnvelope schema freeze。
- [ ] AgentRunEvent schema freeze。
- [ ] MemorySnapshot schema freeze。
- [ ] Mutation operation freeze。
- [ ] Diff schema freeze。
- [ ] Template schema freeze。
- [ ] revision / 409 semantics freeze。
- [ ] OpenAPI endpoint path freeze。
- [ ] Claude-specific資料只存在 adapter layer。
- [ ] Human client 不直接寫 YAML。
- [ ] Agent client 不直接寫 YAML。
- [ ] Guessing catalog 只有 template 能定義。
- [ ] Summarizer 不負責 Agent orchestration。

完成這些後才正式平行開工。

---

## 10. 第一輪 Integration Test

共同先約定一個不需要真實 LLM/Claude 的 end-to-end fixture：

1. 建立 `demo-web` challenge。
2. Human client ingest：
   ```text
   I ran nmap and port 80 is open.
   ```
3. Mock Summarizer 產生：
   ```text
   F001: TCP/80 is open.
   A001: The target may expose an HTTP application.
   ```
4. Memory revision：0 → 1。
5. Person 2 `ctfmem show` 能顯示 F001/A001。
6. Person 3 fake Agent query memory。
7. fake Agent 回傳一段 normalized run log。
8. ingest 後 revision：1 → 2。
9. Human `promote/demote/replace` 使用 expected revision。
10. stale revision 測試收到 409。
11. `guess --seed 42` 能固定抽到相同結果。

只要這個 fixture 通過，三個模組的基本切分即成立。

---

## 11. 共通不做清單

v0 不因任何一人方便而偷偷加入：

- SQLite
- Redis
- vector DB
- embeddings
- knowledge graph
- event-sourcing framework
- message broker
- multi-agent coordinator
- arbitrary YAML patch endpoint
- CLI 直接改 state
- Agent 直接改 state
- realtime collaborative editor
- multi-worker Memory Core
- PTY recorder
- background daemon

需要新增時，先寫 ADR 說明它解決了哪個已觀察到的問題。
