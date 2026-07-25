"""Memory Core v2 — a shared CTF whiteboard service.

Memory Core is **not** an agent framework.  It owns challenge memory, the source journal,
templates, summarization, semantic diff, queries, deterministic guessing, controlled human
mutation, revision semantics and persistence.  It does not own agent orchestration, YOLO
loops, shell execution, CLI/TUI rendering or sandboxing — those belong to the Human Client
and the Agent Bridge, which are HTTP clients like any other.

See ``docs/memory-core-v2.md`` for the full architecture and ``spec/`` for the frozen
contract that the other two components build against.
"""

from .errors import (
    ChallengeExists,
    ChallengeNotFound,
    InvalidRequest,
    MemoryCoreError,
    ProposalInvalid,
    RevisionConflict,
    SummarizerUnavailable,
    TemplateNotFound,
)
from .service import MemoryService
from .summarizer import FakeSummarizer, Summarizer

__version__ = "0.1.0"

__all__ = [
    "ChallengeExists",
    "ChallengeNotFound",
    "FakeSummarizer",
    "InvalidRequest",
    "MemoryCoreError",
    "MemoryService",
    "ProposalInvalid",
    "RevisionConflict",
    "Summarizer",
    "SummarizerUnavailable",
    "TemplateNotFound",
    "__version__",
]
