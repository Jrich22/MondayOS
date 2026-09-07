"""
Turning conclusions into ranked advice.

A recommendation is the only output of this package that is not derivable from the
repository. Facts are read and inferences follow by rule, but "do this next" weighs
evidence against goals, and reasonable people with the same facts will disagree.
That is why `Recommendation` is its own type rather than another `ClaimKind`
variant, and why its confidence is capped below a fact's no matter how much
evidence accumulates.

Two properties make the advice useful rather than merely present.

**Candidates are ranked against each other, not emitted independently.** Anything
that generates advice one item at a time produces a list where every entry sounds
equally urgent. Here every candidate gets a leverage score, and the ones that lose
become the winner's ``instead_of`` — so the reader sees what was considered and
rejected, which is most of what makes advice trustworthy.

**Tradeoffs are attached at construction.** A recommendation with no stated cost is
either trivial or is hiding one. Requiring the field at the point of creation means
the cost has to be articulated by whoever adds the rule, rather than left for the
reader to discover after acting on it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from initiatives.models import Health
from intelligence.evidence import Citation, CitationKind, Evidence
from intelligence.models import NodeKind
from reasoning.confidence import for_recommendation, score
from reasoning.executive import Topic
from reasoning.facts import ProjectFacts
from reasoning.models import Claim, ClaimKind, Gap, Recommendation
from reasoning.options import Option, compare, execution_risk

# Graph node kinds mapped to the citation vocabulary the rest of MondayOS uses,
# so a capability's evidence renders the same way retrieval's does.
_CITATION_FOR: dict[NodeKind, CitationKind] = {
    NodeKind.TASK: CitationKind.TASK,
    NodeKind.DECISION: CitationKind.DECISION,
    NodeKind.FILE: CitationKind.FILE,
    NodeKind.TEST: CitationKind.TEST,
    NodeKind.COMMIT: CitationKind.COMMIT,
    NodeKind.PULL_REQUEST: CitationKind.PULL_REQUEST,
    NodeKind.KNOWLEDGE: CitationKind.KNOWLEDGE,
    NodeKind.SYMBOL: CitationKind.SYMBOL,
}


@dataclass
class Candidate:
    """
    One option under consideration, before ranking.

    ``leverage`` is a rough ordering score, not a measurement. It exists to make
    the comparison explicit and reviewable — a number in a dataclass can be argued
    with, whereas an ordering that emerges from the sequence of if-statements
    cannot.
    """

    statement: str
    rationale: str
    leverage: float
    tradeoffs: tuple[str, ...] = ()
    effort: str = ""
    evidence: Evidence = field(default_factory=Evidence)
    gap_driven: bool = False
    steps: int = 2
    # The capability this work lands in, when it lands in one. Execution risk is
    # mostly a property of where a change happens rather than of the change.
    initiative: Any = None
    greenfield: bool = False


def _from_blocked(facts: ProjectFacts) -> list[Candidate]:
    if not facts.blocked:
        return []
    first = facts.blocked[0]
    return [
        Candidate(
            statement=f"Unblock {first}",
            rationale=(
                f"{len(facts.blocked)} task(s) are blocked. Blocked work consumes "
                "no capacity but holds its dependents, so clearing it is the "
                "cheapest way to restore throughput."
            ),
            # Highest leverage available: unblocking converts already-invested work
            # into shippable work, which nothing else on this list does.
            leverage=0.95,
            tradeoffs=(
                "the blocker may be external, in which case the effort is "
                "coordination rather than engineering",
            ),
            effort="usually small, occasionally not in your control",
            steps=1,
        )
    ]


def _from_in_progress(facts: ProjectFacts) -> list[Candidate]:
    if not facts.in_progress:
        return []
    return [
        Candidate(
            statement=f"Finish {facts.in_progress[0]} before starting anything new",
            rationale=(
                f"{len(facts.in_progress)} task(s) are already in progress. "
                "Work in flight has sunk cost and decaying context; finishing it "
                "realises value that starting something new defers."
            ),
            leverage=0.85 if len(facts.in_progress) > 1 else 0.7,
            tradeoffs=(
                "if the in-progress work is lower value than a new opportunity, "
                "finishing it is sunk-cost reasoning",
            ),
            effort="bounded — the work is already scoped",
            steps=1,
        )
    ]


def _from_gaps(gaps: list[Gap], limit: int = 3) -> list[Candidate]:
    out: list[Candidate] = []
    for gap in gaps[:limit]:
        # Severity 1 gaps outrank severity 3 by a wide margin, but never outrank
        # unblocking work that already exists.
        leverage = {1: 0.8, 2: 0.55, 3: 0.35}.get(gap.severity, 0.4)
        out.append(
            Candidate(
                statement=gap.suggested_task,
                rationale=f"{gap.missing}. {gap.why_it_matters}",
                leverage=leverage,
                tradeoffs=("closes a gap rather than adding user-visible capability",),
                effort="small to moderate",
                evidence=gap.evidence,
                gap_driven=True,
            )
        )
    return out


def _from_inferences(inferences: list[Claim], limit: int = 3) -> list[Candidate]:
    out: list[Candidate] = []
    for claim in inferences[:limit]:
        out.append(
            Candidate(
                statement=f"Address: {claim.statement}",
                rationale=claim.derivation,
                # Scaled by the inference's own confidence: acting on a weakly
                # supported conclusion should rank below acting on a firm one.
                leverage=0.4 + 0.3 * claim.confidence.score,
                tradeoffs=("rests on an inference, not on a stated requirement",),
                evidence=claim.evidence,
                steps=3,
            )
        )
    return out


# Which candidate sources each topic draws on. A topic that pulled from everything
# would answer "what worries you" with a roadmap and "what should we build" with a
# risk register.
_SOURCES: dict[Topic, tuple[str, ...]] = {
    Topic.NEXT_WORK: ("initiatives", "blocked", "in_progress", "gaps", "inferences"),
    Topic.GAPS: ("initiatives", "gaps", "inferences"),
    Topic.RISKS: ("initiatives", "inferences", "blocked"),
    Topic.READINESS: ("initiatives", "gaps", "inferences", "blocked"),
    Topic.INVESTOR: ("initiatives", "gaps", "inferences"),
    Topic.PRIORITIES: ("initiatives", "blocked", "in_progress", "gaps"),
}


def _from_initiatives(found: list[Any]) -> list[Candidate]:
    """
    Candidates drawn from capabilities rather than artefacts.

    These outrank file-level candidates deliberately. "Cue App RSVP is blocked" is
    a product statement a founder can act on; "workspace/service.py is
    under-tested" is an engineering detail that may or may not matter to anything
    anyone is shipping. Reasoning starts at the capability and drills down.
    """
    out: list[Candidate] = []
    for initiative in found:
        milestone = initiative.next_milestone
        if milestone is None:
            continue
        # The capability's own members are the evidence. Omitting this scored the
        # top-ranked recommendation lowest of all — a recommendation ranked first
        # and scored 5% is not a cautious answer, it is an incoherent one.
        evidence = Evidence()
        for member in initiative.members[:12]:
            kind = _CITATION_FOR.get(member.kind, CitationKind.FILE)
            evidence.add(
                Citation(
                    kind=kind,
                    reference=member.node_id,
                    label=member.label,
                    path=member.label if kind is CitationKind.FILE else "",
                    because=member.because,
                )
            )
        # Health drives leverage: a blocked capability outranks a healthy one
        # needing polish, regardless of how tidy the underlying code is.
        leverage = {
            Health.BLOCKED: 0.97,
            Health.STALLED: 0.78,
            Health.AT_RISK: 0.72,
            Health.NOT_STARTED: 0.5,
            Health.HEALTHY: 0.45,
        }.get(initiative.health, 0.4)
        tradeoffs: list[str] = []
        if initiative.risks:
            tradeoffs.append(initiative.risks[0])
        if initiative.dependencies:
            tradeoffs.append(f"touches {len(initiative.dependencies)} dependent capabilit(ies)")
        if not tradeoffs:
            tradeoffs.append("advances one capability while others wait")
        out.append(
            Candidate(
                statement=milestone.statement,
                rationale=(
                    f"{initiative.name} is {initiative.health.value} "
                    f"({initiative.health_because}); {milestone.rationale}"
                ),
                leverage=leverage,
                tradeoffs=tuple(tradeoffs),
                effort="scoped to one capability",
                initiative=initiative,
                greenfield=initiative.health is Health.NOT_STARTED,
                evidence=evidence,
                steps=2,
            )
        )
    return out


def build(
    topic: Topic,
    facts: ProjectFacts,
    inferences: list[Claim],
    gaps: list[Gap],
    initiatives: list[Any] | None = None,
    limit: int = 3,
) -> list[Recommendation]:
    """
    Rank candidates for a topic and return the top few as full recommendations.

    ``limit`` is three because a founder asking what to do next can act on three
    things and cannot act on ten. A longer list is a way of declining to choose
    while appearing thorough.

    Every survivor carries the options that lost and why, plus three separate
    scores — evidence strength, recommendation confidence and execution risk —
    because those come apart constantly and a blended number hides it.
    """
    found = initiatives or []
    pools: dict[str, list[Candidate]] = {
        "initiatives": _from_initiatives(found),
        "blocked": _from_blocked(facts),
        "in_progress": _from_in_progress(facts),
        "gaps": _from_gaps(gaps),
        "inferences": _from_inferences(inferences),
    }

    candidates: list[Candidate] = []
    for source in _SOURCES.get(topic, ("initiatives", "gaps", "inferences")):
        candidates.extend(pools.get(source, []))

    if not candidates:
        return []

    blocked_slugs = frozenset(i.slug for i in found if i.health is Health.BLOCKED)

    options = [
        Option(
            statement=c.statement,
            rationale=c.rationale,
            leverage=c.leverage,
            tradeoffs=c.tradeoffs,
            effort=c.effort,
            initiative=c.initiative,
            greenfield=c.greenfield,
            evidence=c.evidence,
        )
        for c in candidates
    ]
    chosen, alternatives = compare(options, keep=limit)
    by_statement = {c.statement: c for c in candidates}

    out: list[Recommendation] = []
    for index, option in enumerate(chosen):
        candidate = by_statement.get(option.statement)
        steps = candidate.steps if candidate else 2
        gap_driven = candidate.gap_driven if candidate else False
        out.append(
            Recommendation(
                statement=option.statement,
                rationale=option.rationale,
                confidence=for_recommendation(
                    option.evidence or Evidence(),
                    inference_count=steps,
                    gap_driven=gap_driven,
                ),
                # Evidence strength asks a different question from the judgement:
                # how solid is what this rests on, independent of whether acting
                # on it is the right call.
                evidence_strength=score(ClaimKind.FACT, option.evidence or Evidence(), steps=1),
                execution_risk=execution_risk(option, blocked_slugs),
                tradeoffs=option.tradeoffs,
                # Only the leading recommendation carries the rejected set.
                # Repeating it on every entry is noise, and it is the top call
                # whose alternatives a reader actually weighs.
                alternatives=tuple(alternatives) if index == 0 else (),
                effort=option.effort,
                evidence=option.evidence or Evidence(),
                initiative=option.initiative.name if option.initiative else "",
            )
        )
    return out
