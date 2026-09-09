"""
Where one project ends and another begins.

A repository can contain other repositories' worth of work. MondayOS keeps Cue
App and sourcingBOT under `projects/`, and both have their own decision logs,
their own ADR-001, and their own history. Treating those as part of the parent is
not a presentation problem — it makes the wrong evidence *retrievable*, and an
answer that cites another project's decision as your own is worse than one that
cites nothing.

The rule is structural, so it needs no list of which projects a repository
happens to vendor: a directory that **holds** project roots without having source
of its own is a boundary. `projects/` is; `dashboard/`, which has its own
package.json but belongs to this repository, is not.

This lives in `core` because two layers need the same answer and neither may
import the other. `intelligence` scopes decision retrieval with it, and
`initiatives.layout` scopes discovery with it. One definition, so the two cannot
drift into disagreeing about where a project ends.
"""

from __future__ import annotations

import collections
from collections.abc import Iterable

# Files marking the root of a project. Every ecosystem already has one, which is
# why this needs no knowledge of which projects a given repository contains.
PROJECT_MARKERS = frozenset(
    {
        "package.json",
        "pyproject.toml",
        "setup.py",
        "Cargo.toml",
        "go.mod",
        "pom.xml",
        "build.gradle",
        "Gemfile",
        "composer.json",
    }
)


def nested_roots(paths: Iterable[str], source_dirs: frozenset[str]) -> frozenset[str]:
    """
    Directories that hold other projects, as opposed to being one.

    ``paths`` are repository-relative; ``source_dirs`` are the directories that
    directly contain at least one source file. A parent that has source of its
    own is doing work rather than merely holding other people's, and the
    repository root is never a boundary.
    """
    holders: dict[str, set[str]] = collections.defaultdict(set)
    for path in paths:
        segments = path.split("/")
        if len(segments) > 1 and segments[-1] in PROJECT_MARKERS:
            holders["/".join(segments[:-2])].add("/".join(segments[:-1]))

    roots: set[str] = set()
    for parent, children in holders.items():
        if parent and parent not in source_dirs:
            roots |= children
    return frozenset(roots)


def outside(path: str, roots: frozenset[str]) -> bool:
    """Whether a repository-relative path belongs to one of these nested projects."""
    return any(path == root or path.startswith(f"{root}/") for root in roots)
