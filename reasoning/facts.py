"""
Reading the repository into structured numbers.

Inference needs quantities, not prose. "The workspace package has 14 source files
and 2 test files" supports a conclusion; "the workspace package is substantial"
supports nothing, because there is no rule you can apply to it. So this module
produces counts, ratios and identifiers, and leaves every judgement about what
they mean to `reasoning.inference`.

The unit of analysis is the **area** — a top-level package or directory. That
choice is doing real work. A project-wide test ratio is nearly useless: a codebase
can look adequately tested in aggregate while one critical package has no tests at
all, and the aggregate actively hides exactly the thing worth surfacing. Areas are
the coarsest grouping at which "this part is under-tested" is still actionable.

Everything is derived from the existing index, graph, task store and git history.
Nothing is stored, and nothing is read that those four do not already own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from intelligence.evidence import Citation, CitationKind, Evidence
from intelligence.graph import RelationshipGraph
from intelligence.index import ProjectIndex
from intelligence.models import FileKind, NodeKind
from reasoning.confidence import score
from reasoning.models import Claim, ClaimKind

# Directories that are not areas of the product: infrastructure, fixtures and
# vendored code. Counting `tests/` as an area would report it as having no tests.
_NOT_AREAS = frozenset(
    {"tests", "test", "docs", "scripts", "config", "node_modules", "dist", "build", "vendor"}
)

# Below this many source files an area is too small for ratio-based conclusions.
# Three files with no tests is a module someone started; thirty is a liability,
# and applying one rule to both would generate noise that trains people to ignore
# the output.
MIN_AREA_FILES = 4


@dataclass
class Area:
    """One coherent part of the codebase, with everything a rule can test."""

    name: str
    source_files: int = 0
    test_files: int = 0
    lines: int = 0
    symbols: int = 0
    # ADR ids whose text this area's code references.
    decisions: list[str] = field(default_factory=list)
    # Task ids that name this area.
    tasks: list[str] = field(default_factory=list)
    documented: bool = False

    @property
    def test_ratio(self) -> float:
        """Test files per source file. Crude, and honest about being crude."""
        return self.test_files / self.source_files if self.source_files else 0.0

    @property
    def substantial(self) -> bool:
        return self.source_files >= MIN_AREA_FILES

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source_files": self.source_files,
            "test_files": self.test_files,
            "lines": self.lines,
            "symbols": self.symbols,
            "decisions": list(self.decisions),
            "tasks": list(self.tasks),
            "documented": self.documented,
            "test_ratio": round(self.test_ratio, 2),
        }


@dataclass
class ProjectFacts:
    """
    What is true about the project right now.

    ``claims`` is the same information as prose, for the narrator. The structured
    fields are for the rules. Both exist because collapsing them would force
    inference to parse English it just finished generating.
    """

    project: str
    areas: dict[str, Area] = field(default_factory=dict)
    open_tasks: list[str] = field(default_factory=list)
    in_progress: list[str] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    proposed_decisions: list[str] = field(default_factory=list)
    recent_commits: list[str] = field(default_factory=list)
    merged_prs: list[str] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)

    @property
    def substantial_areas(self) -> list[Area]:
        return sorted(
            (a for a in self.areas.values() if a.substantial),
            key=lambda a: -a.source_files,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "areas": {k: v.to_dict() for k, v in self.areas.items()},
            "open_tasks": list(self.open_tasks),
            "in_progress": list(self.in_progress),
            "completed": list(self.completed),
            "blocked": list(self.blocked),
            "decisions": list(self.decisions),
            "proposed_decisions": list(self.proposed_decisions),
            "recent_commits": list(self.recent_commits),
            "merged_prs": list(self.merged_prs),
            "claims": [c.to_dict() for c in self.claims],
        }


def _area_of(path: str) -> str:
    """The top-level directory a path belongs to, or '.' for a root file."""
    head, _, tail = path.partition("/")
    return head if tail else "."


def gather(
    index: ProjectIndex,
    graph: RelationshipGraph,
    tasks: list[dict[str, Any]] | None = None,
) -> ProjectFacts:
    """
    Read the project into facts.

    Tasks arrive as plain dictionaries from the real TaskManager, matching how
    `intelligence.graph.build` takes them — this module reads the task system, it
    does not model it.
    """
    facts = ProjectFacts(project=index.project)

    _gather_areas(facts, index)
    _gather_tasks(facts, tasks or [])
    _gather_decisions(facts, graph)
    _gather_history(facts, graph)
    _state_claims(facts, index)

    return facts


def _gather_areas(facts: ProjectFacts, index: ProjectIndex) -> None:
    for path, entry in index.files.items():
        name = _area_of(path)
        if name in _NOT_AREAS or name.startswith("."):
            # Tests still count, but against the area they exercise rather than as
            # an area of their own. Attribution happens below via the graph.
            continue
        area = facts.areas.setdefault(name, Area(name=name))
        # Cross-references the indexer already extracted from this file's text:
        # a docstring naming ADR-017 is that file citing the decision it
        # implements. Read here rather than walked from the graph because the
        # index holds it directly, and a traversal would find the same thing
        # more slowly and only where an edge happened to be built.
        for reference in entry.references:
            if reference.startswith("ADR-") and reference not in area.decisions:
                area.decisions.append(reference)
            elif reference.startswith("TASK-") and reference not in area.tasks:
                area.tasks.append(reference)
        if entry.kind is FileKind.TEST:
            area.test_files += 1
        elif entry.kind is FileKind.SOURCE:
            area.source_files += 1
            area.lines += entry.lines
            area.symbols += len(entry.symbols)
        elif entry.kind is FileKind.DOCUMENTATION:
            area.documented = True

    # A test file living under tests/ still tests something. Attribute it by name
    # — tests/test_workspace.py exercises workspace/ — because that is the
    # convention this repository actually follows, and a graph edge only exists
    # where an import was detected.
    for path, entry in index.files.items():
        if entry.kind is not FileKind.TEST:
            continue
        stem = path.rsplit("/", 1)[-1].removeprefix("test_").removesuffix(".py")
        for name, area in facts.areas.items():
            if stem and (stem == name or stem.startswith(f"{name}_")):
                area.test_files += 1
                break


def _gather_tasks(facts: ProjectFacts, tasks: list[dict[str, Any]]) -> None:
    for task in tasks:
        task_id = str(task.get("id", "")).strip()
        if not task_id:
            continue
        status = str(task.get("status", "")).lower()
        label = f"{task_id} {task.get('title', '')}".strip()
        if "progress" in status:
            facts.in_progress.append(label)
        elif "block" in status:
            facts.blocked.append(label)
        elif status in ("completed", "done", "closed"):
            facts.completed.append(label)
        else:
            facts.open_tasks.append(label)


def _gather_decisions(facts: ProjectFacts, graph: RelationshipGraph) -> None:
    for node in graph.of_kind(NodeKind.DECISION):
        facts.decisions.append(node.label)
        # A decision still marked Proposed is a decision nobody committed to. That
        # distinction is the difference between an architecture and an intention.
        if "proposed" in node.label.lower():
            facts.proposed_decisions.append(node.label)


def _gather_history(facts: ProjectFacts, graph: RelationshipGraph) -> None:
    facts.recent_commits = [n.label for n in graph.of_kind(NodeKind.COMMIT)][:20]
    facts.merged_prs = [n.label for n in graph.of_kind(NodeKind.PULL_REQUEST)]


def _state_claims(facts: ProjectFacts, index: ProjectIndex) -> None:
    """Turn the counts into typed FACT claims a narrator can quote."""
    source_total = sum(a.source_files for a in facts.areas.values())
    test_total = sum(a.test_files for a in facts.areas.values())

    def cite(path: str, because: str) -> Evidence:
        evidence = Evidence()
        evidence.add(Citation(kind=CitationKind.FILE, reference=path, path=path, because=because))
        return evidence

    if facts.areas:
        shape = Evidence()
        for area in facts.substantial_areas[:6]:
            shape.add(
                Citation(
                    kind=CitationKind.FILE,
                    reference=area.name,
                    path=area.name,
                    because=f"{area.source_files} source files",
                )
            )
        facts.claims.append(
            Claim(
                kind=ClaimKind.FACT,
                statement=(
                    f"{facts.project} has {len(facts.areas)} areas, "
                    f"{source_total} source files and {test_total} test files."
                ),
                derivation="counted from the project index",
                confidence=score(ClaimKind.FACT, shape),
                evidence=shape,
            )
        )

    for group, label in (
        (facts.in_progress, "in progress"),
        (facts.blocked, "blocked"),
        (facts.open_tasks, "open"),
    ):
        if not group:
            continue
        evidence = Evidence()
        for item in group[:5]:
            task_id = item.split(" ", 1)[0]
            evidence.add(
                Citation(
                    kind=CitationKind.TASK,
                    reference=task_id,
                    label=item,
                    because=f"task is {label}",
                )
            )
        facts.claims.append(
            Claim(
                kind=ClaimKind.FACT,
                statement=f"{len(group)} task(s) {label}: {'; '.join(group[:3])}.",
                derivation="read from the task store",
                confidence=score(ClaimKind.FACT, evidence),
                evidence=evidence,
            )
        )

    if facts.decisions:
        evidence = Evidence()
        for label in facts.decisions[:8]:
            adr = label.split(" ", 1)[0]
            evidence.add(
                Citation(kind=CitationKind.DECISION, reference=adr, label=label, because="ADR")
            )
        statement = f"{len(facts.decisions)} architecture decisions are recorded"
        if facts.proposed_decisions:
            statement += f", {len(facts.proposed_decisions)} still Proposed"
        facts.claims.append(
            Claim(
                kind=ClaimKind.FACT,
                statement=statement + ".",
                derivation="read from the decision log",
                confidence=score(ClaimKind.FACT, evidence),
                evidence=evidence,
            )
        )

    if index.files:
        readme = next((p for p in index.files if p.lower().endswith("readme.md")), "")
        if readme:
            facts.claims.append(
                Claim(
                    kind=ClaimKind.FACT,
                    statement=f"The project documents itself in {readme}.",
                    derivation="found in the project index",
                    confidence=score(ClaimKind.FACT, cite(readme, "project README")),
                    evidence=cite(readme, "project README"),
                )
            )
