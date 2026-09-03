"""
The relationship graph — Task → ADR → Code → Test → Commit → PR.

Every edge is derived from something the project actually wrote down:

    Task     → ADR      a task's text names ADR-017
    ADR      → Code     a docstring or comment names ADR-017
    Code     → Test     a test file's name or imports name the module
    Commit   → File     git log --name-only says the commit touched it
    Commit   → Task     a commit message names TASK-0073
    PR       → Commit   a merge commit says "Merge pull request #39"
    Knowledge→ Anything a knowledge entry's components name a project artefact

Nothing is inferred by similarity. An edge produced by "these look related" is a
claim the project never made, and it would be indistinguishable in the output
from one the project did make — which would quietly destroy the value of citing
evidence at all. Every edge carries ``because``: the literal reason it exists.

Traversal is breadth-first with a depth bound, so "show me everything related to
providers" returns a neighbourhood rather than the whole graph.
"""

from __future__ import annotations

import re
import subprocess
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from intelligence.index import ProjectIndex
from intelligence.models import Edge, EdgeKind, FileKind, Node, NodeKind

# How many commits of history to read. Enough to answer "what changed recently"
# and to connect tasks to the work that closed them, without walking years.
COMMIT_LIMIT = 200

# Two heading styles exist in this repository and both are real: MondayOS writes
# "## ADR-001: Title", sourcingBOT writes "## ADR-019 — Title". Accepting only
# one silently drops an entire project's decisions from the graph.
_ADR_HEADING = re.compile(r"^##\s+(ADR-\d{3,})\s*[:\u2014\u2013-]\s*(.+)$", re.M)
_ADR_STATUS = re.compile(r"^\*\*Status:\*\*\s*(.+)$", re.M)
_TASK_REF = re.compile(r"\bTASK-\d{3,}\b")
_ADR_REF = re.compile(r"\bADR-\d{3,}\b")
_PR_MERGE = re.compile(r"Merge pull request #(\d+)")


@dataclass
class RelationshipGraph:
    """Nodes and edges for one project, with adjacency for traversal."""

    project: str
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    _adjacency: dict[str, list[Edge]] = field(default_factory=dict)

    def add_node(self, node: Node) -> None:
        self.nodes.setdefault(node.id, node)

    def add_edge(self, edge: Edge) -> None:
        # Both endpoints must exist. An edge to a node nobody defined is a
        # dangling claim, and silently creating the target would invent one.
        if edge.source not in self.nodes or edge.target not in self.nodes:
            return
        self.edges.append(edge)
        self._adjacency.setdefault(edge.source, []).append(edge)
        self._adjacency.setdefault(edge.target, []).append(edge)

    def neighbours(self, node_id: str) -> list[Edge]:
        return list(self._adjacency.get(node_id, []))

    def related(self, node_id: str, depth: int = 2, limit: int = 40) -> list[tuple[Node, Edge]]:
        """
        The neighbourhood around a node, breadth-first.

        Returns each reached node with the edge that reached it, so a caller can
        always say *why* something appeared in the results.
        """
        if node_id not in self.nodes:
            return []

        seen = {node_id}
        out: list[tuple[Node, Edge]] = []
        queue: deque[tuple[str, int]] = deque([(node_id, 0)])

        while queue and len(out) < limit:
            current, level = queue.popleft()
            if level >= depth:
                continue
            for edge in self._adjacency.get(current, []):
                other = edge.target if edge.source == current else edge.source
                if other in seen:
                    continue
                seen.add(other)
                node = self.nodes.get(other)
                if node is None:
                    continue
                out.append((node, edge))
                queue.append((other, level + 1))
                if len(out) >= limit:
                    break
        return out

    def of_kind(self, kind: NodeKind) -> list[Node]:
        return sorted((n for n in self.nodes.values() if n.kind is kind), key=lambda n: n.id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "nodes": [n.to_dict() for n in self.nodes.values()],
            "edges": [e.to_dict() for e in self.edges],
            "counts": {
                "nodes": len(self.nodes),
                "edges": len(self.edges),
                **{k.value: len(self.of_kind(k)) for k in NodeKind},
            },
        }


def build(
    index: ProjectIndex,
    tasks: Iterable[dict[str, Any]] = (),
    knowledge: Iterable[dict[str, Any]] = (),
    commit_limit: int = COMMIT_LIMIT,
) -> RelationshipGraph:
    """
    Assemble the graph from an index plus the systems that own the other nodes.

    Tasks and knowledge arrive as plain dictionaries from their real stores —
    this module reads them, it does not model them. That is what keeps it from
    becoming a second copy of the task system.
    """
    graph = RelationshipGraph(project=index.project)

    _add_files(graph, index)
    decisions = _add_decisions(graph, index)
    _add_tasks(graph, tasks, decisions)
    _add_knowledge(graph, knowledge)
    _link_code_to_decisions(graph, index, decisions)
    _link_tests_to_code(graph, index)
    _add_history(graph, index, commit_limit)

    return graph


# --------------------------------------------------------------------------- #
# node builders
# --------------------------------------------------------------------------- #


def _add_files(graph: RelationshipGraph, index: ProjectIndex) -> None:
    for path, entry in index.files.items():
        kind = NodeKind.TEST if entry.kind is FileKind.TEST else NodeKind.FILE
        graph.add_node(Node(id=f"file:{path}", kind=kind, label=path, path=path))


def _add_decisions(graph: RelationshipGraph, index: ProjectIndex) -> dict[str, dict[str, str]]:
    """
    ADR nodes, parsed from the project's own decision records.

    **Ids are namespaced by the record that defines them.** A repository can hold
    more than one decision log — MondayOS has `docs/DECISIONS.md` and sourcingBOT
    has `projects/sourcingbot/docs/DECISIONS.md`, and both define an ADR-017.
    Treating those as one node would merge two unrelated decisions and cite the
    wrong one, which is exactly the failure the evidence trail exists to prevent.

    Returns scope → {ADR id → node id}, where scope is the top-level directory
    the decision log lives under. `_scope_of` resolves a reference to the nearest
    log, so a sourcingBOT file naming ADR-017 links to sourcingBOT's ADR-017.
    """
    found: dict[str, dict[str, str]] = {}
    for entry in index.files_of(FileKind.DECISION):
        try:
            text = (index.root / entry.path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        for match in _ADR_HEADING.finditer(text):
            adr_id, title = match.group(1), match.group(2).strip()
            status_match = _ADR_STATUS.search(text, match.end(), match.end() + 400)
            status = status_match.group(1).strip() if status_match else ""
            line = text.count("\n", 0, match.start()) + 1
            scope = _scope_of(entry.path)
            node_id = f"adr:{scope}:{adr_id}" if scope else f"adr:{adr_id}"
            graph.add_node(
                Node(
                    id=node_id,
                    kind=NodeKind.DECISION,
                    label=f"{adr_id}: {title}" + (f" [{status}]" if status else ""),
                    path=entry.path,
                    line=line,
                )
            )
            found.setdefault(scope, {})[adr_id] = node_id
    return found


def _scope_of(path: str) -> str:
    """
    Which sub-project a path belongs to, or "" for the root project.

    `projects/sourcingbot/docs/DECISIONS.md` -> `projects/sourcingbot`.
    Everything else -> "" (the repository's own decisions).
    """
    parts = path.split("/")
    if len(parts) >= 2 and parts[0] == "projects":
        return f"{parts[0]}/{parts[1]}"
    return ""


def _resolve_decision(decisions: dict[str, dict[str, str]], adr_id: str, from_path: str) -> str:
    """
    The ADR node a reference means, given where the reference was written.

    Prefers the decision log in the same sub-project; falls back to the root
    project's. Returns "" when no log defines that id, so an unresolvable
    reference produces no edge rather than a wrong one.
    """
    scope = _scope_of(from_path)
    if scope and adr_id in decisions.get(scope, {}):
        return decisions[scope][adr_id]
    return decisions.get("", {}).get(adr_id, "")


def _add_tasks(
    graph: RelationshipGraph,
    tasks: Iterable[dict[str, Any]],
    decisions: dict[str, dict[str, str]],
) -> None:
    for task in tasks:
        task_id = str(task.get("id", "")).strip()
        if not task_id:
            continue
        node_id = f"task:{task_id}"
        status = str(task.get("status", ""))
        graph.add_node(
            Node(
                id=node_id,
                kind=NodeKind.TASK,
                label=f"{task_id} [{status}] {task.get('title', '')}".strip(),
            )
        )
        # A task naming an ADR is the task citing the decision it implements.
        blob = " ".join(
            str(task.get(f, "")) for f in ("title", "objective", "context", "blocked_reason")
        )
        # Commits the task itself recorded. More reliable than scanning commit
        # messages for task ids: only 5 of the last 60 commits name one, while 21
        # tasks record the commits that closed them.
        for sha in task.get("commit_refs") or []:
            short = str(sha)[:7]
            if short:
                graph.add_edge(
                    Edge(
                        node_id,
                        f"commit:{short}",
                        EdgeKind.REFERENCES,
                        f"{task_id} records commit {short}",
                    )
                )
        for entry_id in task.get("knowledge_refs") or []:
            graph.add_edge(
                Edge(
                    node_id,
                    f"knowledge:{entry_id}",
                    EdgeKind.DOCUMENTS,
                    f"{task_id} records {entry_id}",
                )
            )

        # A task's own project decides which decision log it means.
        task_scope = f"projects/{task.get('project', '')}" if task.get("project") else ""
        for adr in sorted(set(_ADR_REF.findall(blob))):
            target = _resolve_decision(decisions, adr, f"{task_scope}/x" if task_scope else "x")
            if target:
                graph.add_edge(Edge(node_id, target, EdgeKind.IMPLEMENTS, f"{task_id} names {adr}"))


def _add_knowledge(graph: RelationshipGraph, knowledge: Iterable[dict[str, Any]]) -> None:
    for entry in knowledge:
        entry_id = str(entry.get("id", "")).strip()
        if not entry_id:
            continue
        graph.add_node(
            Node(
                id=f"knowledge:{entry_id}",
                kind=NodeKind.KNOWLEDGE,
                label=f"{entry_id} {entry.get('title', '')}".strip(),
            )
        )


# --------------------------------------------------------------------------- #
# edge builders
# --------------------------------------------------------------------------- #


def _link_code_to_decisions(
    graph: RelationshipGraph, index: ProjectIndex, decisions: dict[str, dict[str, str]]
) -> None:
    """A file naming an ADR is the code pointing at the decision behind it."""
    for path, entry in index.files.items():
        if entry.kind is FileKind.DECISION:
            continue
        for ref in sorted(entry.references):
            if not ref.startswith("ADR-"):
                continue
            node = _resolve_decision(decisions, ref, path)
            if node:
                graph.add_edge(
                    Edge(f"file:{path}", node, EdgeKind.REFERENCES, f"{path} names {ref}")
                )


def _link_tests_to_code(graph: RelationshipGraph, index: ProjectIndex) -> None:
    """
    Tests to what they test, by naming convention.

    `tests/test_workspace.py` → `workspace/`, `foo.test.ts` → `foo.ts`. Naming
    convention rather than import analysis: it is the convention this project
    actually follows, and it produces edges that are checkable by reading the
    two filenames. An import graph would find more and would also need a real
    resolver for two languages.
    """
    modules: dict[str, list[str]] = {}
    for path, entry in index.files.items():
        if entry.kind is FileKind.TEST:
            continue
        stem = Path(path).stem.lower()
        modules.setdefault(stem, []).append(path)
        top = path.split("/", 1)[0].lower()
        modules.setdefault(top, []).append(path)

    for path, entry in index.files.items():
        if entry.kind is not FileKind.TEST:
            continue
        stem = Path(path).stem.lower()
        subject = stem.removeprefix("test_").removesuffix(".test").removesuffix(".spec")
        subject = subject.split(".")[0]
        for target in sorted(set(modules.get(subject, [])))[:12]:
            graph.add_edge(
                Edge(
                    f"file:{path}",
                    f"file:{target}",
                    EdgeKind.TESTS,
                    f"{Path(path).name} covers {subject}",
                )
            )


def _add_history(graph: RelationshipGraph, index: ProjectIndex, limit: int) -> None:
    """
    Commits, the files they touched, the tasks they name, and the PRs that merged.

    Read from git, which is the system of record for history. Failure is silent
    and total: a project without a repository simply has no commit nodes, rather
    than an index that refuses to build.
    """
    log = _git(
        index.root,
        "log",
        f"-{limit}",
        "--name-only",
        "--format=%x00%H%x1f%s",
    )
    if not log:
        return

    for block in log.split("\x00"):
        if not block.strip():
            continue
        header, _, body = block.partition("\n")
        sha, _, subject = header.partition("\x1f")
        sha = sha.strip()
        if not sha:
            continue

        short = sha[:7]
        commit_node = f"commit:{short}"
        graph.add_node(
            Node(id=commit_node, kind=NodeKind.COMMIT, label=f"{short} {subject.strip()}")
        )

        # PR ← commit, from the merge commit git itself wrote.
        pr = _PR_MERGE.search(subject)
        if pr:
            pr_node = f"pr:{pr.group(1)}"
            graph.add_node(Node(id=pr_node, kind=NodeKind.PULL_REQUEST, label=f"PR #{pr.group(1)}"))
            graph.add_edge(
                Edge(pr_node, commit_node, EdgeKind.MERGES, f"{short} merges PR #{pr.group(1)}")
            )

        # Commit → task, from the message.
        for task_ref in sorted(set(_TASK_REF.findall(subject))):
            graph.add_edge(
                Edge(
                    commit_node,
                    f"task:{task_ref}",
                    EdgeKind.REFERENCES,
                    f"{short} names {task_ref}",
                )
            )

        # Commit → files it touched.
        for line in body.splitlines():
            path = line.strip()
            if path and path in index.files:
                graph.add_edge(
                    Edge(commit_node, f"file:{path}", EdgeKind.TOUCHES, f"{short} touched {path}")
                )


def _git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, timeout=20, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout if result.returncode == 0 else ""
