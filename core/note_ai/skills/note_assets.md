# Note Assets

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/challenges` | 建立 challenge |
| POST | `/v1/challenges/{id}/ingest` | Ingest input |
| GET | `/v1/challenges/{id}/memory` | 讀取 memory |
| POST | `/v1/challenges/{id}/query` | 自然語言查詢 |
| POST | `/v1/challenges/{id}/guess` | 取得 guessing |
| POST | `/v1/challenges/{id}/mutate` | Human mutation |
| POST | `/v1/challenges/{id}/template` | Template correction |

## Domain Types

```
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

## state.yaml Schema

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
    32-bit ELF challenge...

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

## journal.jsonl Format

```json
{"id":"I-83b70a9a","type":"input","actor":"human","input_type":"note","content":"..."}
{"id":"I-42aa8e92","type":"input","actor":"agent","input_type":"run_log","content":{...}}
{"id":"M-c420e4e1","type":"mutation","actor":"human","reason":"Verified in GDB","operations":[...]}
```

## Template Schema

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

## IngestEnvelope Format

### Human note
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

### CLI result
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

### Agent run log
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
      {"seq": 1, "role": "assistant", "kind": "message", "content": "..."},
      {"seq": 2, "role": "assistant", "kind": "tool_call", "name": "Bash", "content": "checksec ./chall"},
      {"seq": 3, "role": "tool", "kind": "tool_result", "content": "NX enabled ..."}
    ]
  }
}
```

## Diff Response Format

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

## Mutation Operations

```json
{
  "expected_revision": 42,
  "actor": "human",
  "reason": "Verified manually in GDB",
  "operations": [
    {"op": "promote", "id": "A017", "to": "fact"}
  ]
}
```

## Repo Layout

```
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

## Templates

```
templates/
├── generic.yaml
├── web.yaml
├── pwn.yaml
├── rev.yaml
├── crypto.yaml
├── forensics.yaml
└── misc.yaml
```

## CLI Commands

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

## LLM Configuration

```bash
AIS3_BASE_URL=https://llm-api.zoolab.org/v1
AIS3_API_KEY=...
AIS3_SUMMARIZER_MODEL=ais3/nemotron-3-ultra-550b
```
