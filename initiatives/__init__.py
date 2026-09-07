"""
Initiative intelligence — the product layer above the repository.

MondayOS modelled artefacts: files, tasks, commits, decisions, tests. An
initiative is the business capability those artefacts exist to deliver, and it is
the level a founder actually thinks at. "How is Billing going?" is not a question
about any file.

Initiatives are discovered from what the project already states — task title
prefixes, capability documents, packages — and may also be **declared**. The
declared case is what makes roadmap reasoning possible at all: a capability agreed
in planning and not yet started is invisible to every repository signal, and it is
often the most important thing on the board.

Every membership carries the reason it was assigned, so a wrong grouping can be
corrected rather than merely disbelieved.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from initiatives.declare import declare
from initiatives.declare import load as load_declared
from initiatives.declare import save as save_declared
from initiatives.discover import discover
from initiatives.drift import Drift, DriftKind, detect, detect_all
from initiatives.health import assess, link
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
from intelligence.graph import RelationshipGraph
from intelligence.index import ProjectIndex

__all__ = [
    "Basis",
    "Dependency",
    "Drift",
    "DriftKind",
    "Health",
    "Initiative",
    "Member",
    "Milestone",
    "ProjectIndex",
    "Progress",
    "RelationshipGraph",
    "Seed",
    "assess",
    "build",
    "declare",
    "detect",
    "detect_all",
    "discover",
    "link",
    "load_declared",
    "save_declared",
    "slugify",
]


def build(
    index: ProjectIndex,
    graph: RelationshipGraph,
    tasks: list[dict[str, Any]] | None = None,
    config_dir: Path | None = None,
) -> list[Initiative]:
    """
    Every initiative for a project: discovered, assessed, drift-checked and linked.

    The one entry point most callers need. Declarations are read first so a human
    name always wins over a derived one.
    """
    declared = load_declared(config_dir) if config_dir is not None else []
    found = discover(index, graph, tasks=tasks, declared=declared)
    for initiative in found:
        assess(initiative, tasks)
    # After assessment, because drift compares the assessed progress signals
    # against the task record.
    detect_all(found)
    return link(found)
