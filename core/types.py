"""
Shared primitive types for all MondayOS modules.

This module defines only type aliases and simple base types. No business
logic lives here. Every other module may import from core.types; core.types
imports from nothing internal.
"""
from __future__ import annotations

from datetime import datetime
from typing import TypeAlias

# Opaque string identifier for any persistent entity (task, entry, session, agent).
EntityId: TypeAlias = str

# All timestamps in MondayOS are UTC-aware datetimes.
Timestamp: TypeAlias = datetime

# Model identifier string as returned by provider SDKs.
ModelId: TypeAlias = str

# Component name — a top-level module name like "brain", "tasks", "memory".
ComponentName: TypeAlias = str
