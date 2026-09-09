"""
The acceptance gates: properties a program can check, and nothing else.

Each gate is a single sentence about the product that is either true or false
after a run. None of them is a judgement about whether an answer was useful --
that is a human's job, and a number standing in for it would be the least
trustworthy figure in the report and the one people would quote.

Three verdicts, not two. **UNVERIFIABLE** exists because some properties depend
on a provider capability rather than on MondayOS: truncation can only be detected
when the provider says why it stopped, and a local model that stays silent makes
gate 4 unanswerable rather than failed. Recording that honestly is the difference
between a benchmark that runs everywhere and one that only runs where it is
flattered. A gate nobody could check must never read as a gate that passed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Verdict(Enum):
    PASSED = "passed"
    FAILED = "failed"
    # The property could not be observed here. Reported, never scored.
    UNVERIFIABLE = "unverifiable"
    # Nothing in the run exercised it, so there is nothing to conclude.
    NOT_EXERCISED = "not_exercised"


@dataclass
class GateResult:
    number: int
    name: str
    verdict: Verdict
    reason: str = ""
    violations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": self.number,
            "name": self.name,
            "verdict": self.verdict.value,
            "reason": self.reason,
            "violations": self.violations[:20],
            "violation_count": len(self.violations),
        }


GATES: tuple[tuple[int, str], ...] = (
    (1, "Zero cross-project citations"),
    (2, "Zero invented initiatives"),
    (3, "Zero grounded-lookup false positives"),
    (4, "No answer reported complete if output was truncated"),
    (5, "Strategic follow-ups preserve the recommendation"),
    (6, "Quoted confidence/evidence/risk match the assessment"),
    (7, "Stateless 'say more' stays grounded"),
    (8, "Stateful 'say more' continues strategically"),
    (9, "Project git history stays isolated"),
    (10, "No invented ADR or design rationale"),
    (11, "Every line-capable citation resolves to a real line"),
    (12, "No hidden reasoning is persisted"),
    (13, "Every corpus completes the flow"),
    (14, "Recommendation identity is deterministic"),
)


def evaluate(
    turns: list[Any], projects: dict[str, Any], capabilities: dict[str, bool]
) -> list[GateResult]:
    """
    Score every gate over a completed run.

    ``turns`` are every recorded turn across every project; ``projects`` carries
    per-project outcomes; ``capabilities`` says what the provider could report.
    A turn whose failure was the provider's is excluded from scoring here -- it
    was already recorded as an incident, and counting it twice would let someone
    else's outage read as a defect in this one.
    """
    scored = [t for t in turns if t.outcome == "ok"]
    results: list[GateResult] = []

    def add(
        number: int, verdict: Verdict, reason: str = "", violations: list[str] | None = None
    ) -> None:
        name = next(n for i, n in GATES if i == number)
        results.append(GateResult(number, name, verdict, reason, violations or []))

    def by_id(suffix: str) -> list[Any]:
        return [t for t in scored if t.turn_id.endswith(suffix)]

    # 1 — cross-project citations
    bad = [f"{t.project}/{t.turn_id}: {p}" for t in scored for p in t.citations["outside_boundary"]]
    add(
        1,
        Verdict.FAILED if bad else Verdict.PASSED,
        "a citation pointed into a nested project" if bad else "",
        bad,
    )

    # 2 — invented initiatives
    bad = [f"{t.project}/{t.turn_id}: {n}" for t in scored for n in t.initiatives["invented"]]
    add(
        2,
        Verdict.FAILED if bad else Verdict.PASSED,
        "an answer named a capability discovery does not know" if bad else "",
        bad,
    )

    # 3 — grounded lookups that answered strategically
    lookups = [t for t in scored if t.expect_register == "grounded"]
    bad = [
        f"{t.project}/{t.turn_id}: routed {t.observed_register}"
        for t in lookups
        if t.observed_register not in ("grounded", "")
    ]
    add(
        3,
        Verdict.FAILED if bad else (Verdict.PASSED if lookups else Verdict.NOT_EXERCISED),
        "a lookup was answered in the executive register" if bad else "",
        bad,
    )

    # 4 — truncation, only where the provider can report it
    if not capabilities.get("reports_stop_reason"):
        add(
            4,
            Verdict.UNVERIFIABLE,
            "the configured provider does not expose a stop reason, so a truncated "
            "answer is indistinguishable from a finished one",
        )
    else:
        bad = [
            f"{t.project}/{t.turn_id}"
            for t in scored
            if t.stop_reason == "max_tokens" and not t.incomplete
        ]
        add(
            4,
            Verdict.FAILED if bad else Verdict.PASSED,
            "an answer cut off at the token limit was reported complete" if bad else "",
            bad,
        )

    # 5 — a follow-up must not silently re-decide
    bad = []
    for project, record in projects.items():
        anchor = record.get("recommendation_key", "")
        if not anchor:
            continue
        for t in scored:
            if t.project != project or not t.turn_id.startswith(("C.", "D.", "I.say-more-warm")):
                continue
            if t.recommendation_key and t.recommendation_key != anchor:
                bad.append(f"{project}/{t.turn_id}: {anchor!r} became {t.recommendation_key!r}")
    add(
        5,
        Verdict.FAILED if bad else Verdict.PASSED,
        "a follow-up changed the recommendation without being asked to" if bad else "",
        bad,
    )

    # 6 — a quoted number must be the number that was computed
    bad = []
    for t in scored:
        for name, stated in (t.quoted_scores or {}).items():
            actual = t.scores.get(name)
            if actual is None:
                continue
            if abs(stated - actual) > 0.05:
                bad.append(f"{t.project}/{t.turn_id}: said {name}={stated}, computed {actual}")
    add(
        6,
        Verdict.FAILED if bad else Verdict.PASSED,
        "an answer quoted a score that disagrees with the assessment" if bad else "",
        bad,
    )

    # 7 / 8 — the two halves of "say more"
    cold = by_id("I.say-more-cold")
    bad = [
        f"{t.project}: routed {t.observed_register}"
        for t in cold
        if t.observed_register not in ("grounded", "")
    ]
    add(
        7,
        Verdict.FAILED if bad else (Verdict.PASSED if cold else Verdict.NOT_EXERCISED),
        "an elaboration with nothing to continue opened Executive Mode" if bad else "",
        bad,
    )

    warm = by_id("I.say-more-warm")
    bad = [
        f"{t.project}: routed {t.observed_register}"
        for t in warm
        if t.observed_register not in ("continuation", "executive")
    ]
    add(
        8,
        Verdict.FAILED if bad else (Verdict.PASSED if warm else Verdict.NOT_EXERCISED),
        "an elaboration with a decision in play did not continue it" if bad else "",
        bad,
    )

    # 9 — history belongs to the project that made it
    bad = [f"{t.project}/{t.turn_id}: {c}" for t in scored for c in t.foreign_history]
    add(
        9,
        Verdict.FAILED if bad else Verdict.PASSED,
        "an answer cited another project's history" if bad else "",
        bad,
    )

    # 10 — a decision the project never recorded
    bad = [f"{t.project}/{t.turn_id}: {a}" for t in scored for a in t.invented_decisions]
    add(
        10,
        Verdict.FAILED if bad else Verdict.PASSED,
        "an answer named a decision record that does not exist" if bad else "",
        bad,
    )

    # 11 — a line must be a real line
    bad = [f"{t.project}/{t.turn_id}: {v}" for t in scored for v in t.citations["invalid_lines"]]
    add(
        11,
        Verdict.FAILED if bad else Verdict.PASSED,
        "a citation named a line past the end of the file" if bad else "",
        bad,
    )

    # 12 — persistence holds only what was on screen
    bad = [f"{p}: {k}" for p, r in projects.items() for k in r.get("hidden_reasoning_keys", [])]
    add(
        12,
        Verdict.FAILED if bad else Verdict.PASSED,
        "a conversation record contains a field the user never saw" if bad else "",
        bad,
    )

    # 13 — every corpus got through
    bad = [
        f"{p}: {r.get('incomplete_reason', 'did not finish')}"
        for p, r in projects.items()
        if r.get("available") and not r.get("completed")
    ]
    add(
        13,
        Verdict.FAILED if bad else Verdict.PASSED,
        "a corpus did not complete the journey" if bad else "",
        bad,
    )

    # 14 — the same question, the same decision
    bad = []
    for project, record in projects.items():
        repeat = record.get("determinism")
        if not repeat:
            continue
        for field_name, (first, second) in repeat.items():
            if first != second:
                bad.append(f"{project}: {field_name} {first!r} -> {second!r}")
    exercised = any(r.get("determinism") for r in projects.values())
    add(
        14,
        Verdict.FAILED if bad else (Verdict.PASSED if exercised else Verdict.NOT_EXERCISED),
        "the same question against an unchanged project produced a different decision"
        if bad
        else "",
        bad,
    )

    return results


def overall(results: list[GateResult]) -> str:
    """READY, READY WITH KNOWN LIMITATIONS, or NOT READY."""
    if any(r.verdict is Verdict.FAILED for r in results):
        return "NOT READY"
    if any(r.verdict in (Verdict.UNVERIFIABLE, Verdict.NOT_EXERCISED) for r in results):
        return "READY WITH KNOWN LIMITATIONS"
    return "READY"
