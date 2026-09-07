"""
Reading a repository's shape, so discovery does not have to assume MondayOS's.

Initiative discovery used to be calibrated on this repository: a capability was a
top-level directory with at least ten source files. That is true of MondayOS,
which has twenty such packages, and false of almost everything else. Cue App and
sourcingBOT keep every line under `src/`, so the rule found one capability called
"src". WeatherBot's `archive/` and `ops/` have six files each and vanished.

This module replaces those constants with three structural questions, none of
which mentions a directory by name:

**Is there a container?** A directory holding nearly all the source across
several substantial children is a layout convention, not a capability. `src`,
`source`, `app`, `packages` and `backend` all answer yes by shape alone, so none
of them needs to be special-cased -- and a project that puts a real capability in
a directory called `app/` is not punished for the name.

**Where does another project begin?** `projects/` holds Cue App and sourcingBOT.
Neither is a MondayOS capability. The distinction that matters is *holding*
projects versus *being* one: `dashboard/` has its own package.json too, but it
has source of its own and belongs to this repository.

**What recurs across layers?** Under a container, capabilities are not
directories -- they are names that appear in a page, a component tree and a
library module. Counting arbitrary repeats over-generates, because every UI
fragment repeats inside `components/`. Counting distinct *layers* is what
separates a feature from a fragment.

Nothing here decides what an initiative is; that judgement needs evidence, and it
lives in `discover`. This module only reports the shape it found.
"""

from __future__ import annotations

import collections
import re
from dataclasses import dataclass

from intelligence.index import ProjectIndex
from intelligence.models import FileKind, IndexedFile

# Names that describe how code is arranged rather than what it does. Kept small
# and structural on purpose: a long list of domain words would be the overfitting
# this module exists to remove, just relocated. Everything here is a word any
# language's projects use for the same purpose.
STRUCTURAL = frozenset(
    {
        # layout
        "src",
        "source",
        "lib",
        "libs",
        "app",
        "apps",
        "packages",
        "pkg",
        "internal",
        "components",
        "pages",
        "views",
        "routes",
        "handlers",
        "controllers",
        "models",
        "types",
        "utils",
        "util",
        "helpers",
        "common",
        "shared",
        "core",
        "config",
        "constants",
        "styles",
        "assets",
        "static",
        "public",
        "hooks",
        "test",
        "tests",
        "spec",
        "specs",
        "docs",
        "doc",
        "scripts",
        "tools",
        "bin",
        "dist",
        "build",
        "node_modules",
        "vendor",
        "__pycache__",
        "migrations",
        # conventional module names
        "index",
        "main",
        "base",
        "init",
        "errors",
        "exceptions",
        "settings",
        "setup",
        # UI-structure and CRUD words. These name *how* a thing is presented or
        # edited, never what the product does: every application has a Detail
        # view and a Create form, so neither distinguishes one capability from
        # another.
        "badge",
        "card",
        "button",
        "modal",
        "dialog",
        "panel",
        "form",
        "list",
        "detail",
        "create",
        "edit",
        "new",
        "view",
        "item",
        "row",
        "cell",
        "icon",
        "label",
        "menu",
        "nav",
        "header",
        "footer",
        "sidebar",
        "layout",
        "wrapper",
        "container",
        "provider",
        "context",
        "store",
        "hook",
        "service",
        "client",
        "api",
        "schema",
        "widget",
        "field",
    }
)

# Files marking the root of a project. Every ecosystem already has one, which is
# why this needs no knowledge of which projects a given repository vendors.
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

# Build output and dependency trees -- never a capability in any language. This
# is the only directory-name exclusion in discovery; everything else is excluded
# for having no source in it, which is a property rather than a name.
INFRA_DIRS = frozenset(
    {
        ".git",
        ".venv",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        "__pycache__",
        "node_modules",
        "vendor",
        "dist",
        "build",
        "target",
        "coverage",
    }
)

# Plurals English does not form with -s. Deliberately tiny: this is grammar, not
# domain vocabulary, and a long list here would be a stoplist by another name.
IRREGULAR_PLURALS = {
    "people": "person",
    "children": "child",
    "men": "man",
    "women": "woman",
    "data": "datum",
}

# A container holds nearly all the source and has several substantial children.
# The share is high because the question is "is everything in here", not "is a
# lot of it"; a project split between `src/` and `server/` has no container and
# should be read as flat.
CONTAINER_SHARE = 0.80
CONTAINER_MIN_CHILDREN = 2

# Source files that make a directory worth considering at all.
SUBSTANTIAL = 4

# Distinct layers a name must appear in before it reads as a capability.
CO_OCCURRENCE_MIN = 2

# How far discovery will descend through nested containers. `packages/web/src/`
# is real; unbounded descent would make every leaf directory a candidate.
MAX_DESCENT = 2

_EXTENSION = re.compile(r"\.\w+$")
_SEPARATORS = re.compile(r"[_\-\s]+|(?<=[a-z0-9])(?=[A-Z])")


def singular(slug: str) -> str:
    """
    A slug reduced to its singular, so `guests` and `guest` are one capability.

    Cue App uses both `person` and `people` for the same thing. Reporting them as
    two capabilities would double-count one and make the roster look richer than
    the code behind it actually is.
    """
    if slug in IRREGULAR_PLURALS:
        return IRREGULAR_PLURALS[slug]
    if slug.endswith("ies") and len(slug) > 4:
        return f"{slug[:-3]}y"
    if slug.endswith("es") and len(slug) > 3 and slug[-3] in "sxzh":
        return slug[:-2]
    if slug.endswith("s") and not slug.endswith("ss") and len(slug) > 3:
        return slug[:-1]
    return slug


# The structural names in both numbers, so the set matches whichever a project
# uses for its directories -- `component/` and `components/` are the same
# non-capability, and singularising the input alone would smuggle one past.
_STRUCTURAL_FORMS = STRUCTURAL | {singular(word) for word in STRUCTURAL}


_TEST_SUFFIX = re.compile(r"\.(test|spec)$", re.I)


def normalise(token: str) -> str:
    """
    A path segment reduced to a comparable slug.

    camelCase is split before lowercasing, because most of a TypeScript project's
    capability names live in file names: `CandidateWorkspace.tsx` is evidence for
    `candidate`, and folding the case first would leave one opaque word. The
    `.test`/`.spec` suffix goes too, so a test file counts towards the thing it
    tests rather than towards a capability called "test".
    """
    token = _TEST_SUFFIX.sub("", token)
    token = re.sub(r"[_\-]+", " ", token)
    token = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", token)
    return re.sub(r"\s+", "-", token.strip().lower())


def is_structural(slug: str) -> bool:
    """Whether a slug names an arrangement rather than a capability."""
    return slug in _STRUCTURAL_FORMS or singular(slug) in _STRUCTURAL_FORMS


@dataclass(frozen=True)
class Layout:
    """
    A repository's shape: what to look through, and what to look past.

    ``files`` is the index with other projects removed, so every later step reads
    the same scoped view and none of them has to remember to exclude a boundary.
    """

    containers: frozenset[str]
    boundaries: frozenset[str]
    files: dict[str, IndexedFile]

    @property
    def is_flat(self) -> bool:
        return not self.containers


def nested_roots(files: dict[str, IndexedFile]) -> frozenset[str]:
    """
    Directories that *hold* other projects, as opposed to being one.

    `dashboard/` has a package.json and is MondayOS's own frontend; `projects/`
    has no source of its own and every child under it is a project root. Only the
    second is a boundary. Asking "does this directory contain projects, or is it
    one?" is structural, so it stays true for a monorepo, a vendored dependency
    tree, or a repository with no nested projects at all.
    """
    holders: dict[str, set[str]] = collections.defaultdict(set)
    has_own_source: set[str] = set()
    for path, entry in files.items():
        segments = path.split("/")
        if len(segments) > 1 and segments[-1] in PROJECT_MARKERS:
            holders["/".join(segments[:-2])].add("/".join(segments[:-1]))
        if entry.kind is FileKind.SOURCE and len(segments) > 1:
            has_own_source.add("/".join(segments[:-1]))

    roots: set[str] = set()
    for parent, children in holders.items():
        # A directory with source of its own is doing work, not merely holding
        # other people's. The repository root is never a boundary.
        if parent and parent not in has_own_source:
            roots |= children
    return frozenset(roots)


def _source_share(files: dict[str, IndexedFile]) -> dict[str, int]:
    counts: dict[str, int] = collections.Counter()
    for path, entry in files.items():
        if entry.kind is not FileKind.SOURCE:
            continue
        head, _, tail = path.partition("/")
        if not tail or head in INFRA_DIRS or head.startswith("."):
            continue
        counts[head] += 1
    return dict(counts)


def detect_containers(files: dict[str, IndexedFile]) -> frozenset[str]:
    """
    Directories that hold the project rather than being part of it.

    A container earns the name by shape: nearly all the source lives under it,
    and it has several substantial children of its own. That is why `src`, `app`
    and `source` all resolve correctly without appearing in any list, and why a
    project whose `app/` directory really is one capability among many keeps it.
    """
    counts = _source_share(files)
    total = sum(counts.values())
    if not total:
        return frozenset()

    containers: set[str] = set()
    for head, count in counts.items():
        if count / total < CONTAINER_SHARE:
            continue
        children: dict[str, int] = collections.Counter()
        for path, entry in files.items():
            if entry.kind is not FileKind.SOURCE:
                continue
            segments = path.split("/")
            if len(segments) > 2 and segments[0] == head:
                children[segments[1]] += 1
        if sum(1 for n in children.values() if n >= SUBSTANTIAL) >= CONTAINER_MIN_CHILDREN:
            containers.add(head)
    return frozenset(containers)


def analyse(index: ProjectIndex) -> Layout:
    """The shape of one project: its boundaries first, then its containers."""
    boundaries = nested_roots(index.files)
    files = {
        path: entry
        for path, entry in index.files.items()
        if not any(path.startswith(f"{root}/") for root in boundaries)
    }
    return Layout(containers=detect_containers(files), boundaries=boundaries, files=files)


def descend(segments: list[str], containers: frozenset[str]) -> list[str]:
    """The path below any container, bounded so nesting cannot run away."""
    depth = 0
    while depth < MAX_DESCENT and segments and segments[0] in containers:
        segments = segments[1:]
        depth += 1
    return segments


def layer_of(path: str, containers: frozenset[str]) -> str:
    """
    The architectural layer a file sits in: the first segment below any container.

    `src/components/checkin/Card.tsx` is in the `components` layer. A capability
    is a name that appears in several layers; a UI fragment appears many times
    inside one.
    """
    below = descend(path.split("/")[:-1], containers)
    return below[0] if below else ""


def directory_signals(layout: Layout) -> dict[str, tuple[str, int]]:
    """
    Directories substantial enough to be a capability, keyed by slug.

    Returns the spelling the project actually uses alongside the source count, so
    a caller can both cite the evidence and name the thing what its authors do.
    """
    counts: dict[tuple[str, str], int] = collections.Counter()
    for path, entry in layout.files.items():
        if entry.kind is not FileKind.SOURCE:
            continue
        segments = path.split("/")
        if len(segments) < 2 or segments[0] in INFRA_DIRS or segments[0].startswith("."):
            continue
        below = descend(segments[:-1], layout.containers)
        if not below:
            continue
        counts[(singular(normalise(below[0])), below[0])] += 1

    out: dict[str, tuple[str, int]] = {}
    for (slug, surface), count in counts.items():
        if count < SUBSTANTIAL or not slug or is_structural(slug):
            continue
        if slug not in out or count > out[slug][1]:
            out[slug] = (surface, count)
    return out


def cooccurrence_signals(layout: Layout) -> dict[str, tuple[str, frozenset[str]]]:
    """
    Names recurring across distinct architectural layers.

    Under a container this is what building a feature looks like: a page, a
    component tree and a library module all naming the same thing. Counting
    arbitrary locations instead would surface every UI fragment that happens to
    appear in several files inside `components/`.
    """
    layers: dict[str, set[str]] = collections.defaultdict(set)
    surfaces: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)

    for path, entry in layout.files.items():
        if entry.kind not in (FileKind.SOURCE, FileKind.TEST):
            continue
        segments = path.split("/")
        if segments[0] in INFRA_DIRS:
            continue
        layer = layer_of(path, layout.containers) or segments[0]

        names: set[str] = set()
        for segment in segments[:-1]:
            slug = singular(normalise(segment))
            if slug and not is_structural(slug):
                names.add(slug)
                surfaces[slug][segment] += 1
        stem = singular(normalise(_EXTENSION.sub("", segments[-1])))
        if stem and not is_structural(stem):
            names.add(stem)
            # A compound stem names its subject: billing_handler -> billing.
            head = singular(stem.split("-")[0])
            if len(head) > 3 and not is_structural(head):
                names.add(head)
        for name in names:
            layers[name].add(layer)

    out: dict[str, tuple[str, frozenset[str]]] = {}
    for slug, seen in layers.items():
        if len(seen) < CO_OCCURRENCE_MIN:
            continue
        common = surfaces[slug].most_common(1)
        out[slug] = (common[0][0] if common else slug, frozenset(seen))
    return out


def work_tokens(layout: Layout) -> frozenset[str]:
    """
    Every token the code itself uses, in path segments and module stems.

    Discovery uses this to tell a name the project has earned from one that only
    a document asserts.
    """
    tokens: set[str] = set()
    for path, entry in layout.files.items():
        if entry.kind not in (FileKind.SOURCE, FileKind.TEST):
            continue
        for segment in path.split("/"):
            for part in _SEPARATORS.split(_EXTENSION.sub("", segment)):
                token = singular(normalise(part))
                if token:
                    tokens.add(token)
    return frozenset(tokens)
