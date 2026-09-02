"""
workspace.context — OS-level project context assembly.

The public surface is the engine and the snapshot types. Adapters and the budget
are implementation detail: callers ask for a snapshot, they do not assemble one
themselves, because assembling one yourself is how the isolation rules get
reimplemented incorrectly (ADR-017).
"""

from workspace.context.budget import PRIORITY, SOURCE_CAPS, TOTAL_CAP
from workspace.context.engine import ContextEngine
from workspace.context.snapshot import ContextSnapshot, ContextSource, snapshot_id

__all__ = [
    "PRIORITY",
    "SOURCE_CAPS",
    "TOTAL_CAP",
    "ContextEngine",
    "ContextSnapshot",
    "ContextSource",
    "snapshot_id",
]
