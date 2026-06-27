"""Task-to-model routing logic."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from tasks.task import Task


class ModelTier(Enum):
    """
    Available model tiers, ordered by capability and cost.

    Routing assigns a tier first, then selects a specific model within
    that tier based on availability and configuration.
    """

    HIGH = "high"         # claude-opus-4-8, gpt-4o — complex reasoning, code review
    STANDARD = "standard" # claude-sonnet-4-6 — routine coding, summarization
    FAST = "fast"         # claude-haiku-4-5 — classification, routing decisions
    LOCAL = "local"       # ollama/* — privacy-sensitive, offline, no egress


# Default model IDs per tier. Configurable via brain config.
DEFAULT_MODELS: dict[ModelTier, str] = {
    ModelTier.HIGH:     "claude-opus-4-8",
    ModelTier.STANDARD: "claude-sonnet-4-6",
    ModelTier.FAST:     "claude-haiku-4-5-20251001",
    ModelTier.LOCAL:    "ollama/llama3",
}


@dataclass
class RoutingDecision:
    """
    The result of routing a task to a model.

    `reasoning` is always populated — every routing decision is explainable.
    This is logged before task execution so the routing choice is auditable.
    """

    task_id: str
    model_tier: ModelTier
    model_id: str
    reasoning: str  # required — never empty


class Router:
    """
    Selects the appropriate model tier and specific model for a task.

    Routing considers: task type, privacy requirements, cost configuration,
    and (Phase 2) agent capability records. All decisions are logged with
    their reasoning.

    The routing logic follows the heuristic table in docs/ARCHITECTURE.md.
    Defaults to STANDARD tier when no stronger signal is present.

    TODO: Implement route() using task type → tier heuristics.
    TODO: Add privacy_sensitive flag: always routes to LOCAL regardless of quality.
    TODO: Add cost_threshold config: prefer cheaper tier when delta is small.
    TODO: Log every RoutingDecision to structured log before returning.
    TODO: Phase 2 — accept AgentMemory to prefer agents with track records.
    """

    def route(self, task: Task) -> RoutingDecision:
        """
        Select a model tier and specific model for the given task.

        Always returns a populated RoutingDecision with non-empty reasoning.
        Never raises — falls back to STANDARD tier with an explanatory reason
        if no routing signal matches.
        """
        raise NotImplementedError(
            "TODO: implement routing heuristics from docs/ARCHITECTURE.md"
        )
