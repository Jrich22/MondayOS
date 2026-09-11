"""
MondayOS Memory — three-tier persistent context system for AI agents.

Memory gives AI agents access to context that persists across sessions,
tasks, and agent instances. It is distinct from knowledge: memory holds
operational state; knowledge holds learned facts.

Tiers:
    SessionMemory  — volatile, in-process, per-session (an agent's working RAM)
    ProjectMemory  — persistent, Git-tracked, shared across all agents  (designed,
                     not built)
    AgentMemory    — persistent, per-agent, accumulates capability over time
                     (designed, not built)

**Only SessionMemory is exported.** `ProjectMemory` and `AgentMemory` exist as
designs whose every method raises `NotImplementedError`, and until they are built
they are not part of this package's public interface. Exporting them made the
import succeed and the first call fail, which is the worst arrangement available:
a caller reads three working tiers in the docstring, type-checks against them,
and discovers at runtime that two of them are a plan. Importing
`memory.project.ProjectMemory` directly still works for anyone continuing the
implementation — the modules are unchanged.

Tiers that exist implement the MemoryStore Protocol. Code that accepts
MemoryStore works uniformly with any of them.

Reads are always explicit — agents request specific keys, not all memory
at once. This controls context window usage and makes memory access auditable.

See docs/MEMORY_SYSTEM.md for the full three-tier design and rationale.

Public interface:
    SessionMemory  — volatile session context
    MemoryRecord   — the value stored at each key, with provenance metadata
    MemoryStore    — Protocol type for type annotations
"""

from __future__ import annotations

from memory.base import MemoryRecord, MemoryStore
from memory.session import SessionMemory

__all__ = [
    "SessionMemory",
    "MemoryRecord",
    "MemoryStore",
]
