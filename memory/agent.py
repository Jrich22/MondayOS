"""Persistent per-agent memory."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from memory.base import MemoryRecord


class AgentMemory:
    """
    Persistent memory for a specific named agent across its entire lifetime.

    Stored at memory/agent/{agent-id}.md. Accumulates task history, capability
    signals, and reviewer feedback over time. The orchestrator uses this to
    calibrate routing — agents with strong track records in an area get
    preferential assignment for similar tasks.

    Only named, persistent agents have AgentMemory. Stateless one-off agents
    use SessionMemory only.

    TODO: Implement read()/write() — file-based, same format as ProjectMemory.
    TODO: Add append_task_record() — log a completed task to the agent's history.
    TODO: Add append_feedback() — record human reviewer feedback.
    TODO: Expose capability_summary() for the orchestrator's routing logic.
    """

    def __init__(self, agent_id: str, memory_dir: Path) -> None:
        self.agent_id = agent_id
        self._path = memory_dir / "agent" / f"{agent_id}.md"

    def read(self, key: str) -> MemoryRecord | None:
        raise NotImplementedError("TODO: implement file-based agent memory read")

    def write(self, key: str, value: Any, written_by: str, reason: str = "") -> None:
        raise NotImplementedError

    def expire(self, key: str) -> None:
        raise NotImplementedError

    def invalidate(self, key: str, reason: str) -> None:
        raise NotImplementedError

    def keys(self) -> list[str]:
        raise NotImplementedError

    def append_task_record(self, task_id: str, summary: str, outcome: str) -> None:
        """
        Append a completed task record to this agent's history.

        TODO: Read current agent memory file, append record, rewrite.
        """
        raise NotImplementedError

    def append_feedback(self, reviewer: str, rating: str, notes: str) -> None:
        """
        Append reviewer feedback about this agent's output quality.

        TODO: Used by orchestrator to calibrate future routing decisions.
        """
        raise NotImplementedError
