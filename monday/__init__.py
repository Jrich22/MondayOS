"""
MondayOS Public API — `monday`

This is the stable external interface for MondayOS. All external consumers
import from here. Internal modules are implementation details and are not
part of the public contract.

Quick start:
    from monday import Monday

    monday = Monday()
    print(monday.status())

For configuration:
    from monday import Monday, MondayConfig

    monday = Monday(MondayConfig(
        project_root=Path("/my/project"),
        model_tier="high",
    ))

Public surface:
    Monday        — the main API class
    MondayConfig  — optional configuration dataclass
    AskResponse   — return type of Monday.ask()
    LearnResponse — return type of Monday.learn()
    SearchResponse — return type of Monday.search()
    TaskResponse  — return type of Monday.task()
    StatusResponse — return type of Monday.status()
    ModuleStatus  — per-module health in StatusResponse
"""
from __future__ import annotations

from monday.api import Monday
from monday.config import MondayConfig
from monday.types import (
    AskResponse,
    LearnResponse,
    ModuleStatus,
    SearchResponse,
    StatusResponse,
    TaskResponse,
)

__all__ = [
    "Monday",
    "MondayConfig",
    "AskResponse",
    "LearnResponse",
    "SearchResponse",
    "TaskResponse",
    "StatusResponse",
    "ModuleStatus",
]
