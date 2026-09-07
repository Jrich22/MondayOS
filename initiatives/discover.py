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
4. **Layout.** A substantial directory, or a name recurring across several
   architectural layers, is a capability whether or not anyone wrote it down.
   What counts as substantial is read from the repository's shape rather than
   assumed -- see `initiatives.layout`.

Seeds from different sources are then merged, because `workspace/` and
`AI_WORKSPACE.md` are one initiative seen twice.

**Evidence decides existence; documents only decide wording.** A capability needs
concrete work behind it: a directory, a co-occurring name, or a task prefix. A
document may corroborate one and may improve its name, but a document alone
cannot create one -- that is how `research/` came to be called "Research Roadmap"
and `safety/` "Safety Implementation Plan", naming real capabilities after plans
about them. A document may therefore contribute at most one qualifier, and never
a word that describes a document or a process. Declarations are the exception, as
always: a human naming planned-but-unbuilt work is the one source that does not
need code behind it.

Precision is preferred over recall throughout. A missing member is a gap a user
can point out; a wrong member is Monday confidently misdescribing their product.
"""

from __future__ import annotations

import re
from typing import Any

from initiatives import layout as layout_model
from initiatives.models import Initiative, Member, Seed, slugify
from intelligence.graph import RelationshipGraph
from intelligence.index import ProjectIndex
from intelligence.models import FileKind, NodeKind

# Which directories are infrastructure is no longer a list. `initiatives.layout`
# decides it by shape -- a directory with no source in it is not a capability,
# whatever it is called -- which is what lets the same rules read a repository
# this one has never seen.


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

# A prefix must appear on at least this many tasks to count. One task naming
# "Fix: something" is a habit of phrasing, not a capability.
MIN_PREFIX_TASKS = 2

# Qualifiers a document may never contribute to a capability's name. Each
# describes a *document* or a *process* -- what the writing is, or how the work
# is organised -- rather than what the capability does. "Safety Implementation
# Plan" and "Team Workflow" name documents about the work; `safety/` and
# `workflows/` name the work. Words like "system", "engine" and "bot" are absent
# deliberately: those say what kind of thing a capability is, which is part of
# its name.
_DOCUMENT_QUALIFIERS = frozenset(
    {
        "roadmap",
        "plan",
        "checklist",
        "report",
        "notes",
        "proposal",
        "review",
        "spec",
        "rfc",
        "strategy",
        "summary",
        "status",
        "backlog",
        "todo",
        "team",
        "implementation",
        "phase",
        "sprint",
        "epic",
        "initiative",
        "draft",
        "overview",
        "guide",
        "reference",
        "appendix",
        "faq",
        "howto",
        "tutorial",
        "policy",
        "process",
        "runbook",
        "playbook",
        "template",
        "example",
        "demo",
        "assessment",
        "analysis",
        "audit",
        "evaluation",
        "matrix",
        "log",
        "readme",
        "changelog",
        "decisions",
        "standards",
        "conventions",
        "parity",
        "validation",
        "milestones",
        "design",
    }
)

# Transport words. A capability reached over one of these is the same capability:
# `dashboard_api` is how `dashboard` is served, not a second thing the product
# does.
_TRANSPORT = frozenset({"api", "service", "server", "client", "cli", "rpc", "http", "web"})


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
        out.append(
            Seed(
                name=name,
                because=f"{path} documents this capability",
                keywords=[name.lower()],
                authority=2,
            )
        )
    return out


def seeds_from_layout(index: ProjectIndex) -> list[Seed]:
    """
    Capabilities visible in the shape of the repository.

    Two signals, because two layouts. In a flat project a capability is a
    substantial directory. Under a container -- everything inside `src/`, which
    is how most applications are arranged -- directories below the container are
    still read, and so are names that recur across several architectural layers,
    since that is what a feature looks like when it is split into a page, a
    component tree and a library module.

    Neither signal knows anything about this repository. `initiatives.layout`
    decides what a container is by measuring, so a project laid out under `app/`
    or `packages/` is read the same way.
    """
    shape = layout_model.analyse(index)
    out: list[Seed] = []

    for _slug, (surface, count) in sorted(layout_model.directory_signals(shape).items()):
        out.append(
            Seed(
                name=surface,
                because=f"{count} source files under {surface}/",
                paths=[f"{surface}/"] if shape.is_flat else _paths_for(shape, surface),
                keywords=[surface.lower()],
                authority=1,
                weight=count,
                work=True,
            )
        )

    # Co-occurrence answers "what recurs across layers", which only means
    # something when there are layers. In a flat repository the layers are the
    # top-level packages themselves, and a name appearing in two of them is a
    # shared helper -- `session`, `adapter`, `publishing` -- not a capability.
    if shape.is_flat:
        return out

    known = {seed.slug for seed in out}
    for slug, (surface, layers) in sorted(layout_model.cooccurrence_signals(shape).items()):
        if slug in known:
            continue
        names = ", ".join(sorted(layers))
        out.append(
            Seed(
                name=surface,
                because=f"appears in {len(layers)} architectural layers: {names}",
                keywords=[surface.lower()],
                authority=1,
                weight=len(layers),
                work=True,
            )
        )
    return out


def _paths_for(shape: layout_model.Layout, surface: str) -> list[str]:
    """The prefixes a container-nested directory owns, e.g. `src/billing/`."""
    return sorted(
        {
            f"{container}/{surface}/"
            for container in shape.containers
            if any(p.startswith(f"{container}/{surface}/") for p in shape.files)
        }
    )


def _tokens(seed: Seed) -> set[str]:
    """
    A seed's slug as singular tokens.

    A seed keeps the spelling its project uses, so the directory `tasks/` is
    slugged `tasks` while `TASK_SYSTEM.md` is slugged `task-system`. Comparing
    those verbatim finds no overlap and reports one capability as two.
    """
    return {layout_model.singular(part) for part in seed.slug.split("-") if part}


def _admissible_name(target: Seed, candidate: Seed) -> bool:
    """
    Whether a document's title may become the capability's name.

    Code establishes identity; a document may improve how it reads. `workspace/`
    plus `AI_WORKSPACE.md` gives "AI Workspace", which is what people call it.
    `workflows/` plus `TEAM_WORKFLOW.md` gives "Workflows", because "team" is a
    word about who does the work rather than what it is.

    Two bounds, and a qualifier must clear both. At most one added word, so
    `safety/` cannot become "Trading Safety Rails" -- a document's title rather
    than the name the code goes by. And never a document or process word, so
    `research/` cannot become "Research Roadmap".
    """
    extra = _tokens(candidate) - _tokens(target)
    return bool(extra) is False or (len(extra) <= 1 and not (extra & _DOCUMENT_QUALIFIERS))


def _merge(seeds: list[Seed]) -> list[Seed]:
    """
    Collapse seeds that name the same capability.

    `workspace/` and `AI_WORKSPACE.md` are one initiative observed twice. Two
    seeds merge when one's slug appears as a whole token in the other's, which
    catches that pair without collapsing `growth` into `growth-brain` on a shared
    prefix.

    Work-backed seeds are placed first, so a capability is established by code
    and only then renamed by a document -- and only when the document earns it.
    The previous ordering let the highest authority win outright, which is how a
    plan document came to name the package it was written about.
    """
    merged: list[Seed] = []
    # How far the document that supplied each name had to reach. `AGENTS.md` and
    # `AGENT_ROLES.md` both describe `agents/`; the closer title is the better
    # name, and letting the last one seen win would decide it by filename order.
    reach: dict[int, int] = {}
    # The naming clause is held aside and appended once, so a capability whose
    # name was improved twice cites the document it ended up with rather than
    # every document that ever had a claim on it.
    naming: dict[int, str] = {}
    for seed in sorted(seeds, key=lambda s: (not (s.work or s.declared), -s.authority, s.slug)):
        target = None
        tokens = _tokens(seed)
        for existing in merged:
            existing_tokens = _tokens(existing)
            if tokens & existing_tokens and (
                tokens <= existing_tokens or existing_tokens <= tokens
            ):
                target = existing
                break
        if target is None:
            merged.append(seed)
            continue
        # Fold the weaker seed's evidence into the stronger one.
        target.paths = sorted(set(target.paths) | set(seed.paths))
        target.keywords = sorted(set(target.keywords) | set(seed.keywords))
        target.declared = target.declared or seed.declared
        target.weight = max(target.weight, seed.weight)
        target.work = target.work or seed.work
        target.corroborated = True
        if not seed.work and not _admissible_name(target, seed):
            # The document still corroborates; it just does not get to name it.
            target.because = f"{target.because}; {seed.because}"
            continue
        distance = len(_tokens(seed) - _tokens(target))
        if not seed.work and seed.name != target.name and distance < reach.get(id(target), 99):
            # Provenance has to be exact: the document that supplied the name is
            # the document the evidence cites. Recording a different one would
            # make the name unverifiable at precisely the point someone checks.
            reach[id(target)] = distance
            target.name = seed.name
            naming[id(target)] = f"named by {seed.because}"
        else:
            target.because = f"{target.because}; {seed.because}"

    for seed in merged:
        clause = naming.get(id(seed))
        if clause:
            seed.because = f"{seed.because}; {clause}"
    return merged


def _is_transport_of(seed: Seed, others: list[Seed]) -> str:
    """
    The capability this seed is merely a transport for, if any.

    `dashboard_api` beside `dashboard` is one capability and the protocol it is
    reached over, not two things the product does.
    """
    parts = [layout_model.singular(p) for p in seed.slug.split("-")]
    for other in others:
        if other is seed or not other.work:
            continue
        head = [layout_model.singular(p) for p in other.slug.split("-")]
        if parts[: len(head)] == head and parts[len(head) :]:
            if all(part in _TRANSPORT for part in parts[len(head) :]):
                return other.name
    return ""


def _is_fragment_of(seed: Seed, others: list[Seed]) -> str:
    """
    The longer capability this seed is a broken-off piece of, if any.

    A compound stem contributes its head, so `roll-call.tsx` yields `roll` as
    well as `rollcall`. When both survive and one is a prefix of the other, the
    longer name is the capability and the shorter is debris.

    A seed that owns a directory is never debris: `dashboard` is a prefix of
    `dashboard-api` and both are real, so having a path of its own protects a
    seed from being absorbed by a longer neighbour.
    """
    if seed.paths:
        return ""
    for other in others:
        if other is seed or not other.work or other.slug == seed.slug:
            continue
        if other.slug.startswith(seed.slug) and len(other.slug) > len(seed.slug):
            return other.name
    return ""


def _is_project_namespace(seed: Seed, project: str) -> bool:
    """
    Whether this seed is the product's own namespace rather than one of its parts.

    `monday/` in MondayOS is the front door: the API surface and the CLI. Listing
    it beside Reasoning Engine and Growth BOT reports the whole as one of its
    parts. Matched by prefix because a project rarely names its package exactly
    what it names itself, and bounded in length so a short slug cannot swallow an
    unrelated capability.
    """
    own = slugify(project)
    return len(seed.slug) >= 4 and (seed.slug == own or own.startswith(seed.slug))


def _names_segment(path: str, keywords: list[str]) -> bool:
    """
    Whether a path segment or module stem *is* one of these keywords.

    Whole segments only. A substring test would make `checkin` collect
    `checkinsummary.ts`, and the members list is the evidence a user checks.
    """
    segments = path.split("/")
    stem = layout_model.singular(
        layout_model.normalise(segments[-1].rsplit(".", 1)[0].removeprefix("test_"))
    )
    parts = {layout_model.singular(layout_model.normalise(seg)) for seg in segments[:-1]}
    parts.add(stem)
    parts.add(layout_model.singular(stem.split("-")[0]))
    return any(layout_model.singular(layout_model.normalise(k)) in parts for k in keywords)


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

    # A capability found by co-occurrence owns no directory, so its files have to
    # be gathered by name: `pages/checkin.tsx`, `components/checkin/Panel.tsx`
    # and `lib/checkin.ts` are the capability, spread across three layers. Matched
    # on whole path segments so `checkin` does not also collect `checkinsummary`.
    if not seed.paths:
        for path, entry in sorted(index.files.items()):
            if entry.kind not in (FileKind.SOURCE, FileKind.TEST):
                continue
            if _names_segment(path, seed.keywords):
                kind = NodeKind.TEST if entry.kind is FileKind.TEST else NodeKind.FILE
                add(f"file:{path}", kind, path, f"path names '{seed.keywords[0]}'")

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
    seeds += seeds_from_layout(index)

    merged = _merge(seeds)

    out: list[Initiative] = []
    for seed in merged:
        # Two things that look like capabilities but are not: the product's own
        # namespace, and a capability's transport. Both are recognised by shape,
        # so neither needs a list of names to exclude.
        if not seed.declared and _is_project_namespace(seed, index.project):
            continue
        if not seed.declared and _is_transport_of(seed, merged):
            continue
        if not seed.declared and _is_fragment_of(seed, merged):
            continue
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
        # A capability needs concrete work behind it. This replaces a size
        # threshold calibrated on this repository's packages, which dropped
        # WeatherBot's six-file `archive/` and `ops/` while admitting anything
        # large enough here.
        if not seed.declared and not seed.work:
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
