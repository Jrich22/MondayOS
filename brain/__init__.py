"""
MondayOS Brain — top-level orchestrator and system entry point.

Brain is the single entry point for all MondayOS operation. It wires every
subsystem together and exposes a minimal surface to callers. Nothing outside
this module imports TaskManager, KnowledgeStore, SearchEngine, or EventBus
directly — all interaction flows through Brain.

Internal components:
    BrainConfig    — configuration dataclass
    Brain          — the main system coordinator
    Router         — selects the right model for each task
    RoutingDecision — the result of a routing decision (always includes reasoning)
    ModelTier      — available model capability/cost tiers

Public interface:
    Brain          — instantiate once; call execute_task(), create_task()
    BrainConfig    — configuration (load from config/brain.toml in Phase 1.1)
    Router         — model selection (used internally by Brain.execute_task)
    RoutingDecision — logged before every model call
    ModelTier      — HIGH | STANDARD | FAST | LOCAL
"""
from __future__ import annotations

from brain.orchestrator import Brain, BrainConfig
from brain.router import ModelTier, Router, RoutingDecision

__all__ = [
    "Brain",
    "BrainConfig",
    "Router",
    "RoutingDecision",
    "ModelTier",
]
