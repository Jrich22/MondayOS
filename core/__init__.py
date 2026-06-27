"""
MondayOS Core — shared primitive types.

Every other MondayOS module may import from core. Core imports from nothing
internal. This prevents circular dependencies across the module graph.

Public interface:
    EntityId       — opaque string ID for any persistent entity
    Timestamp      — UTC-aware datetime (all times in MondayOS are UTC)
    ModelId        — AI model identifier string
    ComponentName  — top-level module name
"""
from __future__ import annotations

from core.types import ComponentName, EntityId, ModelId, Timestamp

__all__ = [
    "EntityId",
    "Timestamp",
    "ModelId",
    "ComponentName",
]
