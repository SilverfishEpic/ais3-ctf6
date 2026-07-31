# Research Grounding

This document records the papers that influenced the implemented architecture. It is not a
normative contract. If this document and `spec/` disagree, `spec/` wins.

The rule used here is intentionally narrow: each paper contributes one useful design idea,
and the surrounding machinery is accepted only if the current implementation actually
needs it.

## Summary

| Paper | Borrowed idea | Explicitly not adopted |
| --- | --- | --- |
| [CoALA — Cognitive Architectures for Language Agents](https://arxiv.org/abs/2309.02427) | Separate declarative memory from procedural priors. | Full cognitive architecture, autonomous decision process. |
| [A-MEM — Agentic Memory for LLM Agents](https://arxiv.org/abs/2502.12110) | Atomic memory items with stable identity, metadata, and updates. | Vector DB, graph DB, autonomous linking, autonomous history rewriting. |
| [Generative Agents — Interactive Simulacra of Human Behavior](https://arxiv.org/abs/2304.03442) | Raw experience must survive derived summaries. | Reflection trees, planning loop, automatic importance scoring. |
| [LongMemEval — Benchmarking Chat Assistants on Long-Term Interactive Memory](https://arxiv.org/abs/2410.10813) | Knowledge update and abstention are first-class memory requirements. | The benchmark itself and its indexing/retrieval evaluation framework. |
| [CHAP — Collaborative Human-Agent Protocol](https://arxiv.org/abs/2606.09751) | Attribution, append-only evidence, override rationale, revision, diff visibility. | Full CHAP protocol, identity/signature/audit profiles. |

## CoALA

**Paper:** [Cognitive Architectures for Language Agents](https://arxiv.org/abs/2309.02427)

CoALA distinguishes memory components such as working, episodic, semantic, and procedural
memory for language agents.

**How this project uses it:**

- Facts and Assumptions are challenge-specific declarative memory.
- The source journal is episodic input history.
- Template Guessing catalogs are procedural priors: curated techniques one could try, not
  claims about the current challenge.

**What this project rejects:**

- Memory Core is not a full cognitive architecture.
- Memory Core has no autonomous decision procedure.
- Agent planning belongs outside Memory Core.

## A-MEM

**Paper:** [A-MEM: Agentic Memory for LLM Agents](https://arxiv.org/abs/2502.12110)

A-MEM uses structured memory notes, metadata, links, and memory evolution for LLM agents.

**How this project uses it:**

- Memory items are atomic.
- Fact and Assumption IDs are stable while an item remains in the same epistemic layer.
- New evidence may revise an existing item instead of only appending more text.
- Metadata is intentionally light and auditable.

**What this project rejects:**

- No vector database.
- No embedding retrieval.
- No graph database.
- No autonomous global linking.
- No bulk automatic rewriting of stored history.

At CTF challenge scale, dictionary lookup, section filters, and explicit references are
easier to audit than an opaque retrieval graph.

## Generative Agents

**Paper:** [Generative Agents: Interactive Simulacra of Human Behavior](https://arxiv.org/abs/2304.03442)

Generative Agents uses a memory stream, reflection, retrieval, and planning to simulate
believable agent behavior.

**How this project uses it:**

- A derived summary must never replace the raw experience it came from.
- Memory Core writes the journal before calling the Summarizer.
- `state.yaml` is a current semantic snapshot, not the only record of what happened.

**What this project rejects:**

- No reflection tree.
- No planning loop inside Memory Core.
- No automatic importance score.

The project deliberately avoids untested numeric importance scores. If a score is added
later, it needs a concrete requirement and regression tests.

## LongMemEval

**Paper:** [LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive Memory](https://arxiv.org/abs/2410.10813)

LongMemEval evaluates long-term interactive memory with requirements such as knowledge
updates and abstention.

**How this project uses it:**

- Knowledge update is a core operation: new evidence can update, promote, demote, or
  replace earlier understanding.
- Abstention is required: insufficient evidence must not become a Fact.
- Query answers must be grounded in current whiteboard items and return live references.

**What this project rejects:**

- This project does not implement or score against LongMemEval.
- Memory Core does not adopt LongMemEval's indexing/retrieval/reading framework.

The useful requirement is behavioral: the memory must handle updated knowledge and must be
able to say “not known” when the current board does not support an answer.

## CHAP

**Paper:** [Collaborative Human-Agent Protocol (CHAP)](https://arxiv.org/abs/2606.09751)

CHAP proposes a protocol for human-agent shared workspaces, including attribution,
append-only evidence, override records, diffs, handoff, identity, signatures, and audit
profiles.

**How this project uses it:**

- Every human or agent input carries source attribution.
- Raw inputs are preserved in an append-only journal.
- Explicit human overrides require actor and reason.
- Revision-aware writes prevent silent lost updates.
- Semantic diffs are returned to clients instead of being reconstructed by the UI.

**What this project rejects:**

- No full CHAP protocol.
- No identity profile.
- No cryptographic signature profile.
- No transparency-backed audit profile.
- No multi-party collaboration protocol.

This project borrows the accountability shape, not the full standard. The current
single-user local workflow keeps the implementation intentionally smaller.

## Architectural consequences

These research choices map to the current implementation as follows:

- `memory_core/` owns the semantic state, journal, revision, diff, and Summarizer boundary.
- `spec/` defines the public contract clients must use.
- `templates/` contain deterministic procedural Guessing catalogs.
- `human_client/` and `agent_bridge/` are clients of Memory Core, not alternate memory
  writers.
- `state.yaml` is current state; `journal.jsonl` is the source record.
- Facts and Assumptions stay separate because epistemic level is part of the contract, not
  a rendering preference.

The negative decisions matter as much as the borrowed ideas. The project intentionally does
not include a vector database, knowledge graph, autonomous memory evolution, automatic
importance scoring, direct client YAML writes, or a second semantic memory implementation.
