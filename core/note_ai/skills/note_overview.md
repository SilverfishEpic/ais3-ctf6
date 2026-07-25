# Note Overview

## Project Goal

大幅簡化第一版 CTF Player，把「記憶系統」從自主 Agent 架構中抽離，改成一個可被人類與外部 Agent 共用的獨立白板／記憶服務。

**核心原則：** Memory Core 不是 Agent framework；唯一的模型元件是 Summarizer。

## System Architecture

```
┌──────────────────────┐
│      Human CLI       │
│ note/capture/query   │
│ guess/modify/agent   │
└──────────┬───────────┘
           │ HTTP API
           ▼
┌─────────────────────────────────────────┐
│         Memory Core Service             │
│                                         │
│ Input → Journal → Summarizer → Commit   │
│                           │             │
│                           ▼             │
│                      Templates          │
│                                          │
│                    state.yaml           │
└─────────────────────────────────────────┘
           ▲
           │ HTTP API
           │
┌──────────┴───────────────────────────────┐
│      External Agent Bridge              │
│ Claude adapter / future adapters        │
│ normal / YOLO                           │
└─────────────────────────────────────────┘
```

## Three Memory Layers

| Layer | Description |
|-------|-------------|
| **Fact** | 已由直接證據或明確人工 override 確認 |
| **Assumption** | 合理且可測試，但尚未被充分證明 |
| **Guessing** | 由題型 template 提供的 curated technique catalog |

## Data Flow

```
Human input / Agent run log / CLI command result
                    ↓
              append-only journal
                    ↓
                 Summarizer
                    ↓
           Fact / Assumption updates
```

## Team Division

### Person 1 — Memory Core

**負責範圍:**
```
memory_core/
spec/
docs/memory-core-v2.md
```

**內容:**
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

**不負責:**
- Human CLI UX
- TUI
- Claude orchestration
- YOLO
- 真實各類 CTF technique catalog

### Person 2 — Human Interface

**負責範圍:**
```
cli/
tui/   # optional second phase
```

**內容:**
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

**禁止:** 直接寫 `state.yaml`

### Person 3 — Agent Bridge + CTF Templates

**負責範圍:**
```
agent_bridge/
templates/
```

**Agent:**
- Claude adapter
- provider-neutral event normalization
- memory skill
- normal loop
- YOLO loop
- same/new session
- cancel
- run-log ingest

**Template:**
- generic
- web
- pwn
- rev
- crypto
- forensics
- misc
- curated guessing catalog

**禁止:** 直接寫 `state.yaml`

## Implementation Phases

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

## MVP Acceptance Criteria

### Memory Core

- [ ] 能建立 challenge
- [ ] 能保存 human note
- [ ] 能保存 normalized agent run log
- [ ] journal 保留原始 input
- [ ] Summarizer 能產生 validated proposal
- [ ] Fact / Assumption 遵守 epistemic policy
- [ ] Guessing 只取自 template
- [ ] 每次 commit revision +1
- [ ] stale mutation 回 409
- [ ] atomic state write
- [ ] ingest 與 human mutation 不 lost update
- [ ] 每次更新回傳 semantic diff
- [ ] template 能 explicit reclassify
- [ ] query 回答可 reference memory IDs
- [ ] guess 支援 `seed`
- [ ] 核心測試不需要真實 AIS3 API；live test 為 opt-in

### Contract

- [ ] Person 2 可以只看 OpenAPI 就完成 CLI client
- [ ] Person 3 可以只看 OpenAPI + template schema 就完成 Claude bridge 與 templates
- [ ] Person 1 不需要等待另外兩人的實作即可完成 core mock integration tests

## Ablation Design

### Session
- same-session
- new-session

### Memory layer
- no memory
- fact only
- fact + assumption
- fact + assumption + guessing

### Template
- generic
- category-specific

### Guessing
- disabled
- seeded random guessing

### Human intervention
- no intervention
- human note only
- human mutation/override

### Summarizer
journal replay 允許固定 input 比較：
- 不同 prompt
- 不同 model
- 不同 classification policy

## v1 → v2 Mapping

| v1 | v2 |
|----|-----|
| Board `state.yaml` | 三層 semantic whiteboard |
| `facts` | `fact` |
| `hypotheses` | `assumption` |
| playbook | template guessing catalog + Agent skill |
| rawstore | `journal.jsonl` source record |
| digest | 移除 |
| rolling context | 移除 |
| hooks | Memory Core 不使用 |
| Opus commander | External Agent Bridge |
| resume Board | central memory API |
| 人類直接 edit YAML | mutation API |
| jailed Agent orchestration | 不屬於 Memory Core |

## Key Design Decisions

1. **Observation/source record**：人類或 Agent 到底做了什麼
2. **Epistemic memory**：哪些是 Fact，哪些仍只是 Assumption
3. **Procedural prior**：題型 template 允許抽取哪些 Guessing 技術
4. **Execution context**：外部 Agent 是否 same-session、是否 YOLO

這使 central memory 可以被獨立測量、替換與做消融，並讓人類在 Agent 執行期間透過 revision-safe mutation 改變共享白板。

## Branch Strategy

```
redesign/memory-core-v2     Person 1
redesign/human-client-v2    Person 2
redesign/agent-bridge-v2    Person 3
```

三人先以共同 contract commit 作為共同 base，再各自 branch。

## Mock Boundaries

### Person 1 提供
Mock server / fixture with endpoints

### Person 2 提供
CLI 只依賴 OpenAPI，可以先對 mock server 開發

### Person 3 提供
FakeAgentAdapter → IngestEnvelope + 最小 templates (generic.yaml, web.yaml)
