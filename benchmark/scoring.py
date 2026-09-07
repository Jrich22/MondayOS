"""
Turning measurements into a verdict.

Two categories, and the split is the design.

**Assertions** are invariants. Zero cross-project leakage, zero grounded-lookup
false positives, zero citations outside a project root. A violation fails the
run outright, with no tolerance and no threshold, because these are properties
MondayOS either has or does not.

**Observations** are measured rates recorded for comparison. Routing accuracy,
why-decision hit rate, citation navigability, the discovered initiative set. They
move for legitimate reasons, so they are compared against a committed baseline
rather than against an absolute.

Nothing in this module branches on which project it is scoring. Corpus-specific
nouns live in `corpus.py`; the standard applied to the answers is identical
everywhere, which is what makes a comparison between projects mean anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from benchmark.probes import CorpusProbe

# Observations that must never regress. A drop below the committed baseline is a
# failure rather than a note, because these are the numbers the stabilization
# increments exist to raise.
NO_REGRESSION: frozenset[str] = frozenset(
    {"routing_accuracy", "citation_navigability", "retrieval_grounded_rate"}
)

# Tolerance for float comparison only — not a slack allowance. Rates are computed
# from small integer counts, so anything beyond rounding noise is a real move.
EPSILON = 1e-9


@dataclass
class Assertions:
    """Invariants. Any violation fails the run."""

    leaked_commits: int = 0
    grounded_false_positives: int = 0
    citations_outside_root: int = 0

    def violations(self) -> list[str]:
        out = []
        if self.leaked_commits:
            out.append(f"{self.leaked_commits} commit(s) from another project in this evidence")
        if self.grounded_false_positives:
            out.append(
                f"{self.grounded_false_positives} lookup question(s) routed to a strategic register"
            )
        if self.citations_outside_root:
            out.append(f"{self.citations_outside_root} citation(s) point outside the project root")
        return out

    def to_dict(self) -> dict[str, int]:
        return {
            "leaked_commits": self.leaked_commits,
            "grounded_false_positives": self.grounded_false_positives,
            "citations_outside_root": self.citations_outside_root,
        }


@dataclass
class Observations:
    """Measured rates and sets, compared against the committed baseline."""

    routing_accuracy: float = 0.0
    routing_false_negatives: list[str] = field(default_factory=list)
    routing_false_positives: list[str] = field(default_factory=list)
    retrieval_grounded_rate: float = 0.0
    why_decision_hit: bool = False
    citation_navigability: float = 0.0
    citations: dict[str, Any] = field(default_factory=dict)
    initiatives: list[dict[str, str]] = field(default_factory=list)
    known_failing: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "routing_accuracy": round(self.routing_accuracy, 4),
            "routing_false_negatives": sorted(self.routing_false_negatives),
            "routing_false_positives": sorted(self.routing_false_positives),
            "retrieval_grounded_rate": round(self.retrieval_grounded_rate, 4),
            "why_decision_hit": self.why_decision_hit,
            "citation_navigability": round(self.citation_navigability, 4),
            "citations": self.citations,
            "initiatives": self.initiatives,
            "known_failing": self.known_failing,
        }
        return out


def score(probe: CorpusProbe) -> tuple[Assertions, Observations]:
    """Reduce one corpus's measurements to assertions and observations."""
    assertions = Assertions(
        leaked_commits=len(probe.leaked_commits),
        citations_outside_root=probe.citations.outside_root,
    )
    observations = Observations(
        citations=probe.citations.to_dict(),
        citation_navigability=probe.citations.to_dict()["navigability"],
        initiatives=probe.initiatives,
    )

    routing = [r for r in probe.results if r.dimension == "routing"]
    if routing:
        observations.routing_accuracy = sum(r.passed for r in routing) / len(routing)
    for result in routing:
        if result.passed:
            continue
        # A lookup that became strategic is a false positive — the failure
        # direction that actively harms an answer. The reverse is a miss.
        if result.expected == "grounded":
            observations.routing_false_positives.append(result.case_id)
        else:
            observations.routing_false_negatives.append(result.case_id)
    assertions.grounded_false_positives = len(observations.routing_false_positives)

    retrieval = [r for r in probe.results if r.dimension == "retrieval"]
    if retrieval:
        observations.retrieval_grounded_rate = sum(r.passed for r in retrieval) / len(retrieval)
    observations.why_decision_hit = any(
        r.passed for r in retrieval if r.case_id == "retrieval.why-decision"
    )

    # Known failures are recorded individually, not folded into a percentage:
    # S3 and S4 have to be able to show exactly which ones disappeared.
    observations.known_failing = [
        {
            "case_id": r.case_id,
            "dimension": r.dimension,
            "project": r.project,
            "expected": r.expected,
            "observed": r.observed,
            "because": r.because,
            "still_failing": not r.passed,
        }
        for r in probe.results
        if r.known_failing
    ]
    return assertions, observations


def compare(
    project: str, current: dict[str, Any], baseline: dict[str, Any]
) -> tuple[list[str], list[str]]:
    """
    Compare one corpus against its baseline.

    Returns (failures, improvements). An improvement is a failure too — see
    `report.verdict` — because a baseline that silently absorbs good news stops
    describing the system and stops protecting it.
    """
    failures: list[str] = []
    improvements: list[str] = []

    for key in sorted(NO_REGRESSION):
        now = float(current.get(key, 0.0))
        was = float(baseline.get(key, 0.0))
        if now < was - EPSILON:
            failures.append(f"{project}: {key} fell from {was:.4f} to {now:.4f}")
        elif now > was + EPSILON:
            improvements.append(f"{project}: {key} rose from {was:.4f} to {now:.4f}")

    was_initiatives = baseline.get("initiatives", [])
    now_initiatives = current.get("initiatives", [])
    if _names(now_initiatives) != _names(was_initiatives):
        improvements.append(
            f"{project}: discovered initiatives changed from "
            f"{_names(was_initiatives)} to {_names(now_initiatives)}"
        )

    was_failing = {
        k["case_id"] for k in baseline.get("known_failing", []) if k.get("still_failing")
    }
    now_failing = {k["case_id"] for k in current.get("known_failing", []) if k.get("still_failing")}
    for fixed in sorted(was_failing - now_failing):
        improvements.append(f"{project}: known failure {fixed} now passes")
    for broken in sorted(now_failing - was_failing):
        failures.append(f"{project}: {broken} now fails and was not a known failure")

    if current.get("routing_false_positives"):
        failures.append(
            f"{project}: grounded lookups routed strategic: "
            f"{sorted(current['routing_false_positives'])}"
        )
    return failures, improvements


def _names(initiatives: list[dict[str, str]]) -> list[str]:
    return [i.get("name", "") for i in initiatives]
