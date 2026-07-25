# Note Facts

## Core Principle

> **Memory Core 是唯一 semantic-memory writer；Human UI 與 External Agent 都只是 API client。**

## Three Memory Layers

### Fact
對目前 challenge 已由直接證據或明確人工 override 確認的 atomic statement。

**Valid sources:**
- nmap / checksec / file / curl 等工具輸出直接可見結果
- challenge file 中可直接確認的內容
- exploit 成功後可重現的結果
- 人類以 override 明確聲明並提供 reason

**Invalid source:**
- 外部 Agent 的自然語言推論本身不能自動成為 Fact

### Assumption
與目前 challenge 直接相關、合理且可測試，但尚未被充分證明的 atomic proposition。

**Valid sources:**
- Agent 推論
- 人類的非 override 推測
- Summarizer 從多個 observation 得出的待驗證 attack path

### Guessing
由題型 template 提供的 curated technique catalog；不是 Summarizer 自由生成的記憶。

**Examples:** SQL injection, SSRF, SSTI, auth confusion, request smuggling, deserialization

## Evolution Direction

```
Guessing technique → (被選中並形成具體題目假說) → Assumption → (經直接 evidence 驗證) → Fact
```

**FORBIDDEN:** Guessing → (因模型覺得合理) → Fact

## Revision Contract

- 每個 challenge 有 `revision: N`
- 所有 mutation 必須帶 `expected_revision: N`
- 不符合時回傳 `409 Conflict`
- Client 需 reload 後重試

## Epistemic Policy (Summarizer MUST follow)

| Input Type | Classification |
|------------|----------------|
| tool result 中直接可驗證內容 | 可提案 Fact |
| assistant/agent 的推測文字 | Assumption |
| human 普通 note 的推測 | Assumption |
| human explicit override | 可由 mutation API 直接成為 Fact |
| 證據不足 | Assumption 或不新增 |
| template guessing item | 不可自行升格 |
| promotion Assumption → Fact | 必須有新 evidence 或 human override reason |

## Source/Evidence Contract

- Memory Core 必須先把 input 原樣保存至 append-only journal，再交給 Summarizer
- Claude SDK event 不得直接成為 Memory Core 的 domain model
- Person 3 負責: Claude event → AgentRunEvent / IngestEnvelope
- Person 2 負責: human text / process stdout/stderr → IngestEnvelope
- Person 1 只處理標準 envelope

## Concurrency Constraints

### Single Writer
**只有 Memory Core service 可以改 `state.yaml`**

禁止直接寫的元件:
- Human CLI
- TUI
- Agent Bridge
- Claude skill

全部必須經由 API。

### Single Process (MVP)
- MVP 明確限制 Memory Core 使用單一 writer process
- 每個 challenge 在 commit 時需要短時間 critical section
- **不要在 LLM 呼叫期間持有 lock**

### Commit Flow
```
lock
→ check current revision
→ validate proposal/mutation
→ write temp state
→ fsync as appropriate
→ atomic replace
→ increment revision
→ unlock
```

### Summarizer Concurrency
```
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
if revision == R: commit
else: re-read and re-summarize with bounded retry
```

## Template Invariants

- `fact.general` 必須存在
- `assumption.general` 必須存在
- Guess ID 在 template version 內穩定
- reclassification 不改變 Fact/Assumption epistemic level
- 無法映射的 item 放 `general`
- 換題型／換排版，不等於重新判斷真實性

## Reclassification Rules

1. 保留 Fact / Assumption stable ID
2. 不改變 epistemic level
3. 只重新 map section/layout
4. 無法映射的項目進 `general`
5. 換 Guessing catalog
6. 相同 technique ID 可保留 tried state
7. 回傳 semantic diff

## Mutation Operations (Allowed)

- add
- replace
- remove
- promote
- demote
- move

**不提供**任意 YAML patch

## Template Classification

- challenge 第一次有足夠內容時可由 Summarizer 分類
- 一旦選定，不應每次 ingest 自動重新分類
- 之後只有 explicit reclassification 才更換 template

## Normal vs YOLO Mode

### Normal
```
Human prompt → query memory → Agent one run/turn → ingest run log → summarize → diff → stop
```

### YOLO
```
Human prompt → query memory → Agent loop N → ingest → summarize → diff → query updated memory → Agent loop N+1 → ...
```

YOLO 是 Agent Bridge orchestration，不屬於 Memory Core。

## Same-session vs New-session

### Same-session
外部 Agent 每輪延續同一 session。Agent conversation context + central memory。

### New-session
每輪新建 Agent session。current prompt + central memory。

重要 ablation：衡量 central memory 能否承擔跨 run 記憶。

## MVP Non-Goals

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

## Memory Core NOT Responsible For

- Claude Agent SDK orchestration
- YOLO loop
- agent session lifecycle
- CLI/TUI UI state
- terminal PTY recording
- CTF exploitation sandbox
- 自動工具執行
- 多 Agent 協調

## Summarizer NOT Responsible For

- 選擇下一個 exploit tool
- 自主解題
- 執行 shell command
- 自主 YOLO loop
- 修改 Guessing catalog

## Research Basis

| Paper | Adopted | Rejected |
|-------|---------|----------|
| CoALA (Sumers et al., 2023) | Fact/Assumption 分類、Guessing 作為 procedural prior | 完整 Agent cognition |
| A-MEM (Xu et al., 2025) | atomic item、stable ID、metadata | vector DB、embedding、graph DB、autonomous linking |
| Generative Agents (Park et al., 2023) | raw experience 先保存再整理 | - |
| LongMemEval (Wu et al., 2024) | knowledge update、abstention | - |
| CHAP (Shahid et al., 2026) | attribution、diff、override、evidence log | 完整 CHAP protocol |

## Contract Freeze Checklist

- [ ] Fact / Assumption / Guessing 的語意一致
- [ ] IngestEnvelope schema freeze
- [ ] AgentRunEvent schema freeze
- [ ] MemorySnapshot schema freeze
- [ ] Mutation operation freeze
- [ ] Diff schema freeze
- [ ] Template schema freeze
- [ ] revision / 409 semantics freeze
- [ ] OpenAPI endpoint path freeze
- [ ] Claude-specific資料只存在 adapter layer
- [ ] Human client 不直接寫 YAML
- [ ] Agent client 不直接寫 YAML
- [ ] Guessing catalog 只有 template 能定義
- [ ] Summarizer 不負責 Agent orchestration

## Integration Test Fixture

1. 建立 `demo-web` challenge
2. Human client ingest: `I ran nmap and port 80 is open.`
3. Mock Summarizer 產生: F001 (TCP/80 is open), A001 (The target may expose an HTTP application)
4. Memory revision: 0 → 1
5. Person 2 `ctfmem show` 能顯示 F001/A001
6. Person 3 fake Agent query memory
7. fake Agent 回傳一段 normalized run log
8. ingest 後 revision: 1 → 2
9. Human `promote/demote/replace` 使用 expected revision
10. stale revision 測試收到 409
11. `guess --seed 42` 能固定抽到相同結果
