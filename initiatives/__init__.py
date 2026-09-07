"""
Initiative intelligence — the product layer above the repository.

MondayOS modelled artefacts: files, tasks, commits, decisions, tests. An
initiative is the business capability those artefacts exist to deliver, and it is
the level a founder actually thinks at. "How is Billing going?" is not a question
about any file.

This commit introduces the domain model and declaration. Discovery, health
assessment and drift detection build on them.
"""

from __future__ import annotations

from initiatives.declare import declare
from initiatives.declare import load as load_declared
from initiatives.declare import save as save_declared
from initiatives.models import (
    Basis,
    Dependency,
    Health,
    Initiative,
    Member,
    Milestone,
    Progress,
    Seed,
    slugify,
)

__all__ = [
    "Basis",
    "Dependency",
    "Health",
    "Initiative",
    "Member",
    "Milestone",
    "Progress",
    "Seed",
    "declare",
    "load_declared",
    "save_declared",
    "slugify",
]
