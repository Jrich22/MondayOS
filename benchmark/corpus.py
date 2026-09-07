"""
The projects a benchmark run measures, resolved through the normal boundary.

Corpora are discovered through `core.project.ProjectRegistry` and scoped through
`core.vcs`, exactly as a real conversation would resolve them. Nothing here reads
a hard-coded path, because a harness that reaches a project differently from the
product is measuring something the product does not do.

Corpus-specific *nouns* live here — "where is ContextEngine" for one project and
"where is the event list" for another — because a benchmark that asked every
project about the same class name would measure nothing. What must never live
here, or anywhere downstream, is corpus-specific *scoring*: the question changes,
the standard does not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.project import ProjectRegistry
from core.vcs import RepoScope, scope_for

# The projects this benchmark is defined over. Each supplies its own nouns; the
# scoring applied to the answers is identical for all of them.
#
# `symbol` is a definition the project genuinely contains, so "where is X
# implemented" has a right answer. `topic` is a subject its documentation
# genuinely covers.
CORPUS_QUESTIONS: dict[str, dict[str, str]] = {
    "mondayos": {"symbol": "ContextEngine", "topic": "the context engine"},
    "cue-app": {"symbol": "EventCard", "topic": "the event list"},
    "weatherbot": {"symbol": "Recorder", "topic": "the forecast pipeline"},
    "sourcingbot": {"symbol": "Shortlist", "topic": "the shortlist"},
}


@dataclass
class Corpus:
    """One project under measurement."""

    slug: str
    root: Path | None = None
    available: bool = False
    reason: str = ""
    nouns: dict[str, str] = field(default_factory=dict)
    scope: RepoScope | None = None

    # Built lazily and reused across every case for this corpus, so a run does
    # not pay for the index once per question.
    _index: Any = None
    _graph: Any = None
    _engine: Any = None
    _initiatives: Any = None

    def build(self, cache_root: Path) -> None:
        """Index, graph and question engine for this project."""
        from intelligence import QuestionEngine, build_graph, build_index

        if self.root is None:  # pragma: no cover — guarded by `available`
            raise ValueError(f"{self.slug} has no root to index")
        self._index = build_index(self.slug, self.root, cache_root=cache_root / self.slug)
        self._graph = build_graph(self._index, tasks=[], knowledge=[])
        self._engine = QuestionEngine(self._index, self._graph, [])

    @property
    def index(self) -> Any:
        return self._index

    @property
    def graph(self) -> Any:
        return self._graph

    @property
    def engine(self) -> Any:
        return self._engine

    def initiatives(self) -> list[Any]:
        import initiatives as initiative_intelligence

        if self._initiatives is None:
            self._initiatives = list(
                initiative_intelligence.build(self._index, self._graph, [], config_dir=None)
            )
        found: list[Any] = self._initiatives
        return found


def discover(config_dir: Path, slugs: tuple[str, ...] = ()) -> list[Corpus]:
    """
    Resolve every benchmark corpus, recording why any is unavailable.

    A missing project is skipped with a stated reason rather than failing the
    run: a machine without every product checked out should still be able to
    measure the ones it has, and a silent skip would let a corpus quietly stop
    being covered.
    """
    wanted = slugs or tuple(sorted(CORPUS_QUESTIONS))
    try:
        entries = {e.name.lower(): e for e in ProjectRegistry(config_dir).list()}
    except Exception:  # noqa: BLE001 — an unreadable registry is "nothing available"
        entries = {}

    out: list[Corpus] = []
    for slug in wanted:
        nouns = CORPUS_QUESTIONS.get(slug, {})
        entry = entries.get(slug)
        if entry is None:
            out.append(
                Corpus(
                    slug=slug,
                    reason=f"{slug} is not registered in config/projects.json",
                    nouns=nouns,
                )
            )
            continue
        root = Path(entry.path)
        if not root.is_dir():
            out.append(Corpus(slug=slug, root=root, reason=f"{root} does not exist", nouns=nouns))
            continue
        out.append(
            Corpus(
                slug=slug,
                root=root.resolve(),
                available=True,
                nouns=nouns,
                scope=scope_for(root),
            )
        )
    return out
