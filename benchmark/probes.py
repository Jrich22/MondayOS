"""
The measurements. Mechanically checkable only.

Every probe here answers a question with a right answer that does not require
judgement: did this route to the register the case declared, does this citation
point at a file that exists inside the project, does this project's evidence
contain another project's commits.

Deliberately absent: any score for whether a recommendation was *useful*. That
is a real dimension and it is not mechanical, so it stays a human-review step
rather than becoming a number that looks objective and is not.

No probe calls a model provider, constructs a `Monday`, or touches a
`WorkspaceService`. `guard.py` proves it rather than trusting it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from benchmark.cases import ALL_CASES, Dimension
from benchmark.corpus import Corpus
from reasoning.router import ROUTER

# Substrings that identify MondayOS's own engineering work. Used only to detect
# leakage *into another project's* evidence — never to score MondayOS itself, so
# the scoring stays generic.
_LEAK_MARKERS: tuple[str, ...] = (
    "identity policy",
    "sequence allocator",
    "stop indexing mondayos",
    "conversation router",
    "canonical slug",
    "project/repository boundary",
    "scope project intelligence",
    "reasoning layer",
    "initiative intelligence",
)


@dataclass
class CaseResult:
    """One case measured against one corpus."""

    case_id: str
    dimension: str
    project: str
    passed: bool
    expected: str = ""
    observed: str = ""
    known_failing: bool = False
    because: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "dimension": self.dimension,
            "project": self.project,
            "passed": self.passed,
            "expected": self.expected,
            "observed": self.observed,
            "known_failing": self.known_failing,
            "because": self.because,
        }


@dataclass
class CitationBreakdown:
    """
    Where a project's citations point.

    Four buckets rather than a single percentage, because they fail differently:
    a citation outside the project root is an isolation bug, an unresolved one is
    a stale index, and a file-only one is merely less useful than it could be.
    """

    total: int = 0
    navigable: int = 0  # file + line: a reader can open exactly this
    file_only: int = 0  # a real file, but no line
    unresolved: int = 0  # names something that is not on disk
    outside_root: int = 0  # points outside the project — an isolation failure
    # Citations that could carry a line at all. A commit is evidence about a
    # change, not a place in a file; counting it against navigability measures
    # how much history an answer cited rather than how well it pointed. Both
    # rates are reported, because the overall one is what a reader experiences
    # and the scoped one is what the system can actually control.
    line_capable: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "navigable": self.navigable,
            "file_only": self.file_only,
            "unresolved": self.unresolved,
            "outside_root": self.outside_root,
            "line_capable": self.line_capable,
            "navigability": round(self.navigable / self.total, 4) if self.total else 0.0,
            "line_capable_navigability": (
                round(self.navigable / self.line_capable, 4) if self.line_capable else 0.0
            ),
        }


@dataclass
class CorpusProbe:
    """Everything measured for one corpus."""

    project: str
    available: bool
    reason: str = ""
    results: list[CaseResult] = field(default_factory=list)
    citations: CitationBreakdown = field(default_factory=CitationBreakdown)
    initiatives: list[dict[str, str]] = field(default_factory=list)
    leaked_commits: list[str] = field(default_factory=list)
    volatile: dict[str, Any] = field(default_factory=dict)


class _PriorDecision:
    """
    A strategic decision already on the record.

    The minimum the router reads: a topic, and a fingerprint matching the one the
    probe passes, so the decision is current rather than stale. Staleness is its
    own behaviour with its own tests; this fixture is about whether a follow-up
    has anything to follow.
    """

    topic = "next-work"
    fingerprint = "benchmark"


_PRIOR_STATE = _PriorDecision()


def run_routing(corpus: Corpus) -> list[CaseResult]:
    """
    Route every routing case and record the register.

    Routing is a pure function of question text, so the corpus contributes only
    its nouns. Running it per corpus anyway is deliberate: it proves the claim
    that routing does not depend on the project, rather than assuming it.
    """
    out: list[CaseResult] = []
    for case in ALL_CASES:
        if case.dimension is not Dimension.ROUTING:
            continue
        question = case.render(corpus.nouns)
        # A case that names a prior state is routed with one. The state is a
        # fixture rather than the corpus's own, so routing stays a pure function
        # of the question and the state it is given -- which is what lets the
        # same cases run against every project.
        strategy = _PRIOR_STATE if case.with_prior_state else None
        register = ROUTER.route(question, strategy=strategy, fingerprint="benchmark").register.value
        out.append(
            CaseResult(
                case_id=case.id,
                dimension=case.dimension.value,
                project=corpus.slug,
                passed=register == case.expect_register,
                expected=case.expect_register,
                observed=register,
                known_failing=case.known_failing,
                because=case.because,
            )
        )
    return out


def run_retrieval(corpus: Corpus) -> tuple[list[CaseResult], CitationBreakdown]:
    """Ask every retrieval case and classify the evidence it returned."""
    out: list[CaseResult] = []
    breakdown = CitationBreakdown()
    root = corpus.root

    for case in ALL_CASES:
        if case.dimension is not Dimension.RETRIEVAL:
            continue
        question = case.render(corpus.nouns)
        answer = corpus.engine.ask(question)

        if case.expect_cites_kind:
            kinds = {c.kind.value for c in answer.evidence.citations}
            passed = case.expect_cites_kind in kinds
            observed = f"kinds={sorted(kinds)}" if kinds else "no citations"
        else:
            passed = answer.grounded is bool(case.expect_grounded)
            observed = f"grounded={answer.grounded} cites={len(answer.evidence.citations)}"

        out.append(
            CaseResult(
                case_id=case.id,
                dimension=case.dimension.value,
                project=corpus.slug,
                passed=passed,
                expected=(
                    f"cites {case.expect_cites_kind}"
                    if case.expect_cites_kind
                    else f"grounded={case.expect_grounded}"
                ),
                observed=observed,
                known_failing=case.known_failing,
                because=case.because,
            )
        )
        _classify_citations(answer, root, breakdown)

    return out, breakdown


def _classify_citations(answer: Any, root: Path | None, breakdown: CitationBreakdown) -> None:
    for citation in answer.evidence.citations:
        breakdown.total += 1
        # A commit or pull request names a change, not a place in a file. It is
        # counted in the total a reader sees, and excluded from the rate that
        # measures how well the system points at code.
        if citation.kind.value not in ("commit", "pull-request"):
            breakdown.line_capable += 1
        if not citation.path:
            # Artefact references — a commit sha, a task id — name something real
            # without being a file. Counted, but not as a path failure.
            breakdown.file_only += 1
            continue
        resolved = (root / citation.path) if root else Path(citation.path)
        try:
            inside = root is None or resolved.resolve().is_relative_to(root)
        except (OSError, ValueError):
            inside = False
        if not inside:
            breakdown.outside_root += 1
            continue
        if not resolved.exists():
            breakdown.unresolved += 1
            continue
        if citation.line:
            breakdown.navigable += 1
        else:
            breakdown.file_only += 1


def run_isolation(corpus: Corpus, own_markers: bool) -> list[str]:
    """
    Commits in this project's evidence that describe another project's work.

    Checked against citations *and* graph nodes, because COMMIT and PULL_REQUEST
    nodes feed initiative membership and drift detection — evidence can leak
    through the graph without ever appearing in an answer.

    ``own_markers`` is passed by the caller for the project the markers describe,
    so this function stays generic: it never asks which corpus it is looking at.
    """
    if own_markers:
        return []

    from intelligence.models import NodeKind

    labels = [n.label for n in corpus.graph.of_kind(NodeKind.COMMIT)]
    labels += [n.label for n in corpus.graph.of_kind(NodeKind.PULL_REQUEST)]
    answer = corpus.engine.ask("What changed recently?")
    labels += [c.label for c in answer.evidence.citations]

    leaked = []
    for label in labels:
        lowered = label.lower()
        for marker in _LEAK_MARKERS:
            if marker in lowered:
                leaked.append(label)
                break
    return sorted(set(leaked))


def run_discovery(corpus: Corpus) -> list[dict[str, str]]:
    """
        The full ordered initiative set — names and slugs, not a count.

        Order and identity both matter: S3 has to be able to show exactly which
        capability appeared or disappeared, and a count alone would hide a rename.

    `implementation_size` excludes commits and pull requests, which is what makes
        this recordable at all. History grows: an initiative gains a member every
        time someone writes a commit message containing its name, so both the count
        and the ordering used to drift with the git log — recording a baseline was
        itself enough to invalidate it, because the commit that recorded it mentioned
        `benchmark`. That is the same reason file counts and timings live in
        `volatile`.
    """
    return [
        {
            "name": i.name,
            "slug": i.slug,
            "health": i.health.value,
            "members": str(i.implementation_size),
        }
        for i in corpus.initiatives()
    ]


def probe(corpus: Corpus, cache_root: Path, own_markers: bool = False) -> CorpusProbe:
    """Measure one corpus end to end."""
    if not corpus.available:
        return CorpusProbe(project=corpus.slug, available=False, reason=corpus.reason)

    started = time.perf_counter()
    corpus.build(cache_root)
    index_ms = (time.perf_counter() - started) * 1000

    results = run_routing(corpus)
    retrieval_started = time.perf_counter()
    retrieval, citations = run_retrieval(corpus)
    retrieval_ms = (time.perf_counter() - retrieval_started) * 1000
    results.extend(retrieval)

    return CorpusProbe(
        project=corpus.slug,
        available=True,
        results=results,
        citations=citations,
        initiatives=run_discovery(corpus),
        leaked_commits=run_isolation(corpus, own_markers),
        # Latency and corpus size move as a project grows. Recorded so a human
        # reading a diff can see why an observation shifted; never compared.
        volatile={
            "files": len(corpus.index.files),
            "symbols": len(corpus.index.symbols),
            "graph_nodes": len(corpus.graph.nodes),
            "index_ms": round(index_ms),
            "retrieval_ms": round(retrieval_ms),
        },
    )
