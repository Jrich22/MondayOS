"""
MondayOS Memory — three-tier persistent context system for AI agents.

Memory gives AI agents access to context that persists across sessions,
tasks, and agent instances. It is distinct from knowledge: memory holds
operational state; knowledge holds learned facts.

Tiers:
    SessionMemory  — volatile, in-process, per-session (an agent's working RAM)
    ProjectMemory  — persistent, Git-tracked, shared across all agents (project state)
    AgentMemory    — persistent, per-agent, accumulates capability over time

All three tiers implement the MemoryStore Protocol. Code that accepts
MemoryStore works uniformly with any tier.

Reads are always explicit — agents request specific keys, not all memory
at once. This controls context window usage and makes memory access auditable.

See docs/MEMORY_SYSTEM.md for the full three-tier design and rationale.

Public interface:
    SessionMemory  — volatile session context (fully implemented in Phase 1)
    ProjectMemory  — cross-session project state (placeholder, Phase 1)
    AgentMemory    — per-agent history and capability (placeholder, Phase 1)
    MemoryRecord   — the value stored at each key, with provenance metadata
    MemoryStore    — Protocol type for type annotations
"""
from __future__ import annotations

from memory.agent import AgentMemory
from memory.base import MemoryRecord, MemoryStore
from memory.project import ProjectMemory
from memory.session import SessionMemory

__all__ = [
    "SessionMemory",
    "ProjectMemory",
    "AgentMemory",
    "MemoryRecord",
    "MemoryStore",
]
