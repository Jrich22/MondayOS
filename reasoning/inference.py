"""
Turning facts into conclusions, by stated rule.

Each rule reads the structured facts and, when its condition holds, emits an
INFERENCE carrying the rule that produced it. That last part is the design: a
conclusion whose rule is visible can be *rejected*. If Monday says the workspace
package is under-tested and names the rule as "14 source files against 2 test
files, below the 0.15 threshold", a reader who knows those two test files are
exhaustive can dismiss it in a second. The same conclusion delivered as an opinion
would have to be argued with instead.

Rules are conservative on purpose. Every one of them is a heuristic that will
sometimes be wrong — file counts are a poor proxy for test coverage, an area
without an ADR may simply be obvious — and the honest response to that is to
price it into the confidence rather than to suppress the inference or to state it
firmly. A system that only says what is certain says almost nothing, which is
where this one started.

The thresholds are constants at the top rather than literals in the rules, because
they are product judgements, not facts, and they should be arguable in one place.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from intelligence.evidence import Citation, CitationKind, Evidence
from reasoning.confidence import score
from reasoning.facts import Area, ProjectFacts
from reasoning.models import Claim, ClaimKind

# Test files per source file, below which an area reads as under-tested. Low
# deliberately: this should fire on areas with almost nothing, not on areas whose
# testing style differs from the repository average.
UNDER_TESTED = 0.15

# Share of all source files in one area, above which the codebase is concentrated
# there. Concentration is not a defect — it is usually just where the product is —
# but it identifies what breaking would hurt most.
CONCENTRATION = 0.35

# In-progress tasks above which attention is plausibly split. Three is generous
# for one person and tight for a team, which is why the inference says "may be"
# and the confidence is not high.
PARALLEL_WORK = 3


@dataclass(frozen=True)
class Rule:
    """One named inference rule. Named so its output can cite it."""

    name: str
    apply: Callable[[ProjectFacts], list[Claim]]


def _area_evidence(area: Area, because: str) -> Evidence:
    evidence = Evidence()
    evidence.add(
        Citation(kind=CitationKind.FILE, reference=area.name, path=area.name, because=because)
    )
    for adr in area.decisions[:3]:
        evidence.add(
            Citation(kind=CitationKind.DECISION, reference=adr, because="decides this area")
        )
    for task in area.tasks[:3]:
        evidence.add(
            Citation(kind=CitationKind.TASK, reference=task, because="task names this area")
        )
    return evidence


def _under_tested(facts: ProjectFacts) -> list[Claim]:
    out: list[Claim] = []
    for area in facts.substantial_areas:
        if area.test_ratio >= UNDER_TESTED:
            continue
        evidence = _area_evidence(
            area, f"{area.source_files} source files, {area.test_files} test files"
        )
        out.append(
            Claim(
                kind=ClaimKind.INFERENCE,
                statement=(
                    f"{area.name} is under-tested relative to its size — "
                    f"{area.source_files} source files against {area.test_files} test files."
                ),
                derivation=(
                    f"rule: test-file ratio {area.test_ratio:.2f} is below the "
                    f"{UNDER_TESTED} threshold for an area with "
                    f"{area.source_files}+ source files"
                ),
                confidence=score(ClaimKind.INFERENCE, evidence, steps=2),
                evidence=evidence,
                from_facts=(f"{area.name}: {area.source_files} source, {area.test_files} test",),
            )
        )
    return out[:3]


def _undecided(facts: ProjectFacts) -> list[Claim]:
    out: list[Claim] = []
    for area in facts.substantial_areas:
        if area.decisions:
            continue
        evidence = _area_evidence(area, f"{area.source_files} source files, no ADR references")
        out.append(
            Claim(
                kind=ClaimKind.INFERENCE,
                statement=(
                    f"{area.name} carries no recorded architecture decision, "
                    f"despite {area.source_files} source files and {area.symbols} definitions."
                ),
                derivation=(
                    "rule: a substantial area whose code references no ADR has "
                    "design rationale that exists only in whoever wrote it"
                ),
                confidence=score(ClaimKind.INFERENCE, evidence, steps=2),
                evidence=evidence,
            )
        )
    return out[:3]


def _concentration(facts: ProjectFacts) -> list[Claim]:
    total = sum(a.source_files for a in facts.areas.values())
    if total < 10:
        return []
    biggest = max(facts.areas.values(), key=lambda a: a.source_files, default=None)
    if biggest is None or biggest.source_files / total < CONCENTRATION:
        return []
    share = biggest.source_files / total
    evidence = _area_evidence(biggest, f"{biggest.source_files} of {total} source files")
    return [
        Claim(
            kind=ClaimKind.INFERENCE,
            statement=(
                f"{biggest.name} holds {share:.0%} of the codebase, "
                "making it the area where a regression would cost most."
            ),
            derivation=(
                f"rule: one area exceeding {CONCENTRATION:.0%} of source files "
                "concentrates risk there"
            ),
            confidence=score(ClaimKind.INFERENCE, evidence, steps=2),
            evidence=evidence,
        )
    ]


def _proposed_drift(facts: ProjectFacts) -> list[Claim]:
    if not facts.proposed_decisions:
        return []
    evidence = Evidence()
    for label in facts.proposed_decisions[:5]:
        evidence.add(
            Citation(
                kind=CitationKind.DECISION,
                reference=label.split(" ", 1)[0],
                label=label,
                because="status is Proposed",
            )
        )
    return [
        Claim(
            kind=ClaimKind.INFERENCE,
            statement=(
                f"{len(facts.proposed_decisions)} architecture decision(s) remain Proposed, "
                "so the written architecture and the built one may have diverged."
            ),
            derivation=(
                "rule: a decision never moved to Accepted was either abandoned "
                "or implemented without the record catching up"
            ),
            confidence=score(ClaimKind.INFERENCE, evidence, steps=2),
            evidence=evidence,
        )
    ]


def _blocked(facts: ProjectFacts) -> list[Claim]:
    if not facts.blocked:
        return []
    evidence = Evidence()
    for label in facts.blocked[:5]:
        evidence.add(
            Citation(
                kind=CitationKind.TASK,
                reference=label.split(" ", 1)[0],
                label=label,
                because="task is blocked",
            )
        )
    return [
        Claim(
            kind=ClaimKind.INFERENCE,
            statement=(
                f"{len(facts.blocked)} task(s) are blocked, which is the most "
                "immediate constraint on throughput."
            ),
            derivation="rule: blocked work is stalled by definition and unblocks nothing else",
            confidence=score(ClaimKind.INFERENCE, evidence, steps=1),
            evidence=evidence,
        )
    ]


def _split_focus(facts: ProjectFacts) -> list[Claim]:
    if len(facts.in_progress) <= PARALLEL_WORK:
        return []
    evidence = Evidence()
    for label in facts.in_progress[:6]:
        evidence.add(
            Citation(
                kind=CitationKind.TASK,
                reference=label.split(" ", 1)[0],
                label=label,
                because="in progress",
            )
        )
    return [
        Claim(
            kind=ClaimKind.INFERENCE,
            statement=(
                f"{len(facts.in_progress)} tasks are in progress at once, "
                "which may be spreading attention thinner than it needs to be."
            ),
            derivation=(
                f"rule: more than {PARALLEL_WORK} concurrent in-progress tasks "
                "usually means several are not actually moving"
            ),
            confidence=score(ClaimKind.INFERENCE, evidence, steps=2),
            evidence=evidence,
        )
    ]


def _undocumented(facts: ProjectFacts) -> list[Claim]:
    out: list[Claim] = []
    for area in facts.substantial_areas:
        if area.documented:
            continue
        evidence = _area_evidence(area, "no documentation file in this area")
        out.append(
            Claim(
                kind=ClaimKind.INFERENCE,
                statement=(
                    f"{area.name} has no documentation of its own, so it is "
                    "readable only by reading its source."
                ),
                derivation=(
                    "rule: a substantial area with no doc file has no entry point for a reader"
                ),
                confidence=score(ClaimKind.INFERENCE, evidence, steps=2),
                evidence=evidence,
            )
        )
    return out[:2]


RULES: tuple[Rule, ...] = (
    Rule("blocked-work", _blocked),
    Rule("under-tested", _under_tested),
    Rule("undecided-architecture", _undecided),
    Rule("proposed-drift", _proposed_drift),
    Rule("concentration", _concentration),
    Rule("split-focus", _split_focus),
    Rule("undocumented", _undocumented),
)


def infer(facts: ProjectFacts, rules: tuple[Rule, ...] = RULES) -> list[Claim]:
    """
    Run every rule and return what fired, strongest first.

    A rule that raises is skipped rather than allowed to fail the assessment. One
    broken heuristic should cost its own conclusion, not the whole answer — the
    alternative is that a founder asking what to build next gets a stack trace.
    """
    out: list[Claim] = []
    for rule in rules:
        try:
            out.extend(rule.apply(facts))
        except Exception:  # noqa: BLE001 — a heuristic must never break the answer
            continue
    out.sort(key=lambda c: -c.confidence.score)
    return out
