"""Cross-package exceptions shared by the stable MondayOS facade."""
from __future__ import annotations


class TeamCheckpointError(RuntimeError):
    """A required team-run identity checkpoint could not be persisted."""
