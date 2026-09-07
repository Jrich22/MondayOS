"""
Deriving initiatives from what the project already says about itself.

The temptation here is clustering: embed everything, group by similarity, name the
groups. That is rejected for the same reason the index rejects embeddings — a
grouping nobody can explain is a grouping nobody can correct, and the first time
Monday puts a file in the wrong capability the user needs to know *why* to fix it.

So every initiative comes from something the repository states outright:

1. **Declaration.** A human named it. Highest authority, and the only source that
   can describe work not yet begun.
2. **Task title prefixes.** `Cue App: Roll Call Command Center` says which
   capability that task serves, in the words of whoever wrote it.
3. **Initiative documents.** `docs/AI_WORKSPACE.md` is somebody having decided
   this capability was worth a document.
4. **Packages.** A substantial top-level directory is a capability whether or not
   anyone wrote it down.

Seeds from different sources are then merged, because `workspace/` and
`AI_WORKSPACE.md` are one initiative seen twice. The human-written name wins:
"AI Workspace" is what people call it, `workspace` is where it happens to live.

Precision is preferred over recall throughout. A missing member is a gap a user
can point out; a wrong member is Monday confidently misdescribing their product.
"""

from __future__ import annotations

import re
from typing import Any

from initiatives.models import Initiative, Member, Seed, slugify
from intelligence.graph import RelationshipGraph
from intelligence.index import ProjectIndex
from intelligence.models import FileKind, NodeKind

# Documents that describe how the project works rather than what it delivers.
# Treating ENGINEERING_STANDARDS.md as a capability would invent an initiative
# nobody is building.
_NOT_INITIATIVE_DOCS = frozenset(
    {
        "readme",
        "changelog",
        "decisions",
        "architecture",
        "architecture-diagram",
        "engineering-standards",
        "documentation-standards",
        "project-overview",
        "cli",
        "agents",
        "agent-roles",
        "providers",
        "knowledge-runtime-policy",
        "contributing",
        "license",
        "security",
        "roadmap",
        "beta-roadmap",
        "mks",
    }
)

# Directories that are infrastructure rather than capability.
_NOT_INITIATIVE_DIRS = frozenset(
    {
        "tests",
        "test",
        "docs",
        "scripts",
        "config",
        "node_modules",
        "dist",
        "build",
        "vendor",
        "logs",
        "knowledge",
        "tasks",
        # A container of separately-registered products, not a capability of
        # this one. Its contents are other projects with their own initiatives.
        "projects",
    }
)

# An artefact identifier rather than a capability name. `tasks/completed/
# TASK-0051.md` is documentation by file kind, but "Task 0051" is not something
# anyone is building — it is one unit of work inside something that is.
_ARTEFACT_STEM = re.compile(r"^[a-z]{2,12}[-_ ]?\d{2,}$", re.I)


# Directories that hold one document per artefact rather than per capability.
# Deliberately not the package exclusion set: `docs/` belongs there and must not
# be filtered here, since it is where capability documents actually live.
_ARTEFACT_DOC_DIRS = frozenset(
    {"tasks", "knowledge", "logs", "agents", "conversations", "screenshots", "workspace"}
)


# A task title of the form "Cue App: Roll Call". The prefix names the capability.
# Bounded length so a sentence containing a colon is not mistaken for a prefix.
_TITLE_PREFIX = re.compile(r"^\s*([A-Z][\w&/ .-]{2,38}?)\s*:\s+\S")

# Below this many source files, a directory is a module rather than a capability.
MIN_PACKAGE_FILES = 4

# A prefix must appear on at least this many tasks to count. One task naming
# "Fix: something" is a habit of phrasing, not a capability.
MIN_PREFIX_TASKS = 2

# A package with no document and no task prefix naming it needs to be this large
# before it reads as a capability rather than a module. Without this every
# directory becomes an initiative and the roster stops being a product view —
# which is the failure mode that makes the whole layer useless.
STANDALONE_PACKAGE_FILES = 10


# Short all-caps tokens are acronyms and must survive naming intact. Title-casing
# them produces "Ai Workspace", which is not what anyone calls it and makes the
# derived name look broken in exactly the place a user first sees it.
ACRONYM_MAX = 3


def _doc_name(path: str) -> str:
    """`docs/AI_WORKSPACE.md` -> `AI Workspace`."""
    stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    words = []
    for word in stem.replace("-", "_").split("_"):
        if not word:
            continue
        words.append(word if word.isupper() and len(word) <= ACRONYM_MAX else word.capitalize())
    return " ".join(words)


def seeds_from_tasks(tasks: list[dict[str, Any]]) -> list[Seed]:
    counts: dict[str, list[str]] = {}
    for task in tasks:
        match = _TITLE_PREFIX.match(str(task.get("title", "")))
        if not match:
            continue
        prefix = match.group(1).strip()
        counts.setdefault(prefix, []).append(str(task.get("id", "")))
    return [
        Seed(
            name=prefix,
            because=f"{len(ids)} task titles begin '{prefix}:'",
            keywords=[prefix.lower()],
            authority=3,
        )
        for prefix, ids in sorted(counts.items())
        if len(ids) >= MIN_PREFIX_TASKS
    ]


def seeds_from_docs(index: ProjectIndex) -> list[Seed]:
    out: list[Seed] = []
    for path, entry in sorted(index.files.items()):
        if entry.kind is not FileKind.DOCUMENTATION:
            continue
        # A document inside an infrastructure directory describes work, not a
        # capability. Every file under tasks/ is documentation by file kind, and
        # without this each one becomes an initiative named after a task id.
        if any(segment in _ARTEFACT_DOC_DIRS for segment in path.split("/")[:-1]):
            continue
        stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if _ARTEFACT_STEM.match(stem.replace("_", "-")):
            continue
        name = _doc_name(path)
        if slugify(name) in _NOT_INITIATIVE_DOCS:
            continue
        out.append(
            Seed(
                name=name,
                because=f"{path} documents this capability",
                keywords=[name.lower()],
                authority=2,
            )
        )
    return out


def seeds_from_packages(index: ProjectIndex) -> list[Seed]:
    counts: dict[str, int] = {}
    for path, entry in index.files.items():
        head, _, tail = path.partition("/")
        if not tail or head in _NOT_INITIATIVE_DIRS or head.startswith("."):
            continue
        if entry.kind is FileKind.SOURCE:
            counts[head] = counts.get(head, 0) + 1
    return [
        Seed(
            name=name,
            because=f"{count} source files under {name}/",
            paths=[f"{name}/"],
            keywords=[name.lower()],
            authority=1,
            weight=count,
        )
        for name, count in sorted(counts.items())
        if count >= MIN_PACKAGE_FILES
    ]


def _merge(seeds: list[Seed]) -> list[Seed]:
    """
    Collapse seeds that name the same capability.

    `workspace/` and `AI_WORKSPACE.md` are one initiative observed twice. Two seeds
    merge when one's slug appears as a whole token in the other's, which catches
    that pair without collapsing `growth` into `growth-brain` on a shared prefix.
    The higher-authority name survives, so the human-written one wins.
    """
    merged: list[Seed] = []
    for seed in sorted(seeds, key=lambda s: (-s.authority, s.slug)):
        target = None
        tokens = set(seed.slug.split("-"))
        for existing in merged:
            existing_tokens = set(existing.slug.split("-"))
            if tokens & existing_tokens and (
                tokens <= existing_tokens or existing_tokens <= tokens
            ):
                target = existing
                break
        if target is None:
            merged.append(seed)
            continue
        # Fold the weaker seed's evidence into the stronger one's name.
        target.paths = sorted(set(target.paths) | set(seed.paths))
        target.keywords = sorted(set(target.keywords) | set(seed.keywords))
        target.because = f"{target.because}; {seed.because}"
        target.declared = target.declared or seed.declared
        target.weight = max(target.weight, seed.weight)
        target.corroborated = True
    return merged


def _matches(text: str, keywords: list[str]) -> str:
    """The keyword this text contains, or empty. Whole-phrase, case-insensitive."""
    lowered = text.lower()
    for keyword in keywords:
        if keyword and keyword in lowered:
            return keyword
    return ""


def _gather(
    seed: Seed,
    index: ProjectIndex,
    graph: RelationshipGraph,
    tasks: list[dict[str, Any]],
) -> list[Member]:
    members: list[Member] = []
    seen: set[str] = set()

    def add(node_id: str, kind: NodeKind, label: str, because: str) -> None:
        if node_id in seen:
            return
        seen.add(node_id)
        members.append(Member(node_id=node_id, kind=kind, label=label, because=because))

    # Files, by owned path prefix. The strongest signal available: a path is not
    # a guess about what something is for.
    for path, entry in sorted(index.files.items()):
        for prefix in seed.paths:
            if path.startswith(prefix):
                kind = NodeKind.TEST if entry.kind is FileKind.TEST else NodeKind.FILE
                add(f"file:{path}", kind, path, f"lives under {prefix}")
                break

    # Tests kept in a central tests/ directory still test something. Attributed by
    # the convention this repository actually follows — tests/test_workspace.py
    # exercises workspace/ — because otherwise every package with external tests
    # reports as untested, which is both wrong and the exact signal health leans on.
    owned = {prefix.rstrip("/").rsplit("/", 1)[-1] for prefix in seed.paths}
    for path, entry in sorted(index.files.items()):
        if entry.kind is not FileKind.TEST or any(path.startswith(p) for p in seed.paths):
            continue
        stem = path.rsplit("/", 1)[-1].removeprefix("test_").removesuffix(".py")
        for name in owned:
            if stem == name or stem.startswith(f"{name}_"):
                add(f"file:{path}", NodeKind.TEST, path, f"tests {name}/ by naming convention")
                break

    for task in tasks:
        title = str(task.get("title", ""))
        task_id = str(task.get("id", ""))
        hit = _matches(title, seed.keywords)
        if hit and task_id:
            add(
                f"task:{task_id}",
                NodeKind.TASK,
                f"{task_id} [{task.get('status', '')}] {title}",
                f"task title names '{hit}'",
            )

    for node in graph.of_kind(NodeKind.DECISION):
        hit = _matches(node.label, seed.keywords)
        if hit:
            add(node.id, NodeKind.DECISION, node.label, f"decision title names '{hit}'")

    for node in graph.of_kind(NodeKind.COMMIT):
        hit = _matches(node.label, seed.keywords)
        if hit:
            add(node.id, NodeKind.COMMIT, node.label, f"commit message names '{hit}'")

    for node in graph.of_kind(NodeKind.PULL_REQUEST):
        hit = _matches(node.label, seed.keywords)
        if hit:
            add(node.id, NodeKind.PULL_REQUEST, node.label, f"pull request names '{hit}'")

    # Documentation naming the initiative, wherever it lives.
    for path, entry in sorted(index.files.items()):
        if entry.kind is not FileKind.DOCUMENTATION:
            continue
        hit = _matches(path.replace("_", " ").replace("-", " "), seed.keywords)
        if hit:
            add(f"file:{path}", NodeKind.FILE, path, f"document name contains '{hit}'")

    return members


def discover(
    index: ProjectIndex,
    graph: RelationshipGraph,
    tasks: list[dict[str, Any]] | None = None,
    declared: list[Seed] | None = None,
) -> list[Initiative]:
    """
    Every initiative MondayOS can see, declared ones first.

    Declared seeds are never dropped, even when nothing implements them: an
    initiative on the roadmap with no code is a fact about the roadmap, and the
    most useful one this module produces.
    """
    rows = tasks or []
    seeds = list(declared or [])
    seeds += seeds_from_tasks(rows)
    seeds += seeds_from_docs(index)
    seeds += seeds_from_packages(index)

    out: list[Initiative] = []
    for seed in _merge(seeds):
        members = _gather(seed, index, graph, rows)
        # A derived seed that found nothing was a bad guess and is dropped. A
        # declared one is kept: its emptiness is the finding.
        if not members and not seed.declared:
            continue
        # A capability is something being built. A derived seed whose only member
        # is its own document is a document — "Runbook" and "Data Model" are real
        # files and real topics, but nobody is shipping them, and listing them
        # beside AI Workspace makes the roster stop meaning anything.
        if not seed.declared and not any(
            m.kind in (NodeKind.TASK, NodeKind.DECISION)
            or (m.kind is NodeKind.FILE and not m.label.lower().endswith(".md"))
            or m.kind is NodeKind.TEST
            for m in members
        ):
            continue
        # A package nothing else corroborates is a module unless it is large. This
        # is what keeps the roster a product view rather than a directory listing.
        if (
            not seed.declared
            and seed.authority == 1
            and not seed.corroborated
            and seed.weight < STANDALONE_PACKAGE_FILES
        ):
            continue
        out.append(
            Initiative(
                slug=seed.slug,
                name=seed.name,
                declared=seed.declared,
                summary=seed.summary,
                because=seed.because,
                members=members,
            )
        )
    out.sort(key=lambda i: (not i.declared, -len(i.members), i.slug))
    return out
