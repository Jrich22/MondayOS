"""
The vocabulary of a reasoning result.

MondayOS could already retrieve well and hallucinate little. What it could not do
was *conclude* anything: retrieval hands back documents, and a model asked to
summarise documents produces a summary, not a decision. The gap was never
retrieval quality. It was that nothing in the system had permission to say "and
therefore".

Giving it that permission safely is the whole design problem, because the
property that kept hallucinations low was a standing instruction never to infer.
Deleting that instruction would buy a strategic voice at the cost of the
trustworthiness that made the strategic voice worth having.

The resolution is to **type every claim**. An inference is safe to make when it
is labelled an inference, carries the facts it was derived from, and states the
rule that produced it. A reader can then audit the step rather than trust it.
That is why `Claim` has a `kind` and a `derivation` and not merely text: an
unlabelled conclusion and a fact look identical on a screen, and the whole
distinction this system rests on would be invisible at exactly the moment it
mattered.

Nothing here asks a model for anything. An `Assessment` is built deterministically
from the project index, the relationship graph, tasks and git history; the model's
job is to *narrate* it. That inversion — the system reasons, the model explains —
is what makes reasoning a MondayOS capability rather than a prompt that happens to
work today and drifts tomorrow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from intelligence.evidence import Evidence


class ClaimKind(Enum):
    """
    What kind of statement this is — the distinction the whole layer rests on.

    Ordered from most to least directly supported. A reader who knows only this
    ordering already knows how much weight each claim can bear.
    """

    # Directly readable from the repository. "TASK-0074 is in progress" is a fact
    # because the task file says so; disagreeing with it means the file changed.
    FACT = "fact"
    # Concluded from facts by a stated rule. "The workspace package is under-tested"
    # is not written anywhere — it follows from counting code files against test
    # files, and the rule that produced it travels with it.
    INFERENCE = "inference"
    # A judgement about what should happen next. Never derivable from the
    # repository alone: it weighs facts against goals, and reasonable people can
    # disagree. Kept a separate kind so it can never be mistaken for either.
    RECOMMENDATION = "recommendation"


class Band(Enum):
    """
    A coarse confidence label, for surfaces that should not show a percentage.

    Three bands, not five: the difference between 62% and 68% is noise dressed as
    precision, and offering it invites readers to act on a distinction the
    evidence cannot support.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# Band thresholds. HIGH requires genuine corroboration from several independent
# source kinds, which in practice means code plus tests plus a written decision.
_HIGH = 0.75
_MEDIUM = 0.45


@dataclass(frozen=True)
class Confidence:
    """
    How strongly the evidence supports a claim, and why.

    ``because`` is not optional decoration. A bare "78%" is unfalsifiable — the
    reader can neither check it nor learn anything from it being wrong. A score
    that says *"three independent source kinds agree; no contradicting evidence;
    supporting files changed this week"* can be argued with, and a number you can
    argue with is the only kind worth showing.

    Scores are computed by `reasoning.confidence`, never supplied by a model. A
    model asked for a percentage produces a plausible-looking one, which is worse
    than no number at all: it carries the authority of arithmetic with none of
    the arithmetic.
    """

    score: float
    because: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # Clamp rather than raise. A confidence calculation that throws would take
        # down an answer that is otherwise fine, and every input here is already
        # bounded by construction — this is a guard against arithmetic drift, not
        # a validation boundary.
        object.__setattr__(self, "score", max(0.0, min(1.0, float(self.score))))

    @property
    def band(self) -> Band:
        if self.score >= _HIGH:
            return Band.HIGH
        if self.score >= _MEDIUM:
            return Band.MEDIUM
        return Band.LOW

    @property
    def percent(self) -> int:
        return int(round(self.score * 100))

    def display(self) -> str:
        return f"{self.percent}% ({self.band.value})"

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 3),
            "percent": self.percent,
            "band": self.band.value,
            "because": list(self.because),
        }


@dataclass
class Claim:
    """
    One statement, typed, evidenced, and scored.

    ``derivation`` is what separates this from a sentence in a chat window: it
    names the rule that produced the claim, so a reader can reject the rule
    rather than having to trust the conclusion. For a FACT it names the source
    that was read; for an INFERENCE, the reasoning step applied.
    """

    kind: ClaimKind
    statement: str
    confidence: Confidence
    # The rule or reading that produced this. "counted 14 source files against 2
    # test files" — checkable, not "analysis suggests".
    derivation: str = ""
    evidence: Evidence = field(default_factory=Evidence)
    # Statements this was concluded from, by their text. Shallow on purpose: a
    # full provenance DAG is a lot of machinery for a chain that is almost always
    # one step, and the derivation string already names the rule.
    from_facts: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "statement": self.statement,
            "derivation": self.derivation,
            "confidence": self.confidence.to_dict(),
            "evidence": self.evidence.to_dict(),
            "from_facts": list(self.from_facts),
        }


@dataclass
class Gap:
    """
    Something the project is missing, expressed as work rather than as an absence.

    Reporting "there is no testing strategy" ends the conversation; proposing
    "write a testing strategy covering the workspace package, which has 14 source
    files and 2 test files" starts one. The difference is `suggested_task`, and it
    is the reason this type exists instead of a list of strings.

    ``severity`` exists so that a missing launch checklist and a missing docstring
    are not presented as equally urgent — a gap list that does not rank itself is
    a backlog nobody triages.
    """

    subject: str
    missing: str
    why_it_matters: str
    suggested_task: str
    severity: int = 2  # 1 highest, 3 lowest
    evidence: Evidence = field(default_factory=Evidence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "missing": self.missing,
            "why_it_matters": self.why_it_matters,
            "suggested_task": self.suggested_task,
            "severity": self.severity,
            "evidence": self.evidence.to_dict(),
        }


@dataclass
class Recommendation:
    """
    What to do next, what it was chosen over, and what it costs to be wrong.

    Three separate scores, because they answer different questions and routinely
    disagree. ``evidence_strength`` measures the facts underneath; ``confidence``
    measures the call itself; ``execution_risk`` measures the doing.

    "Add tests to payments" has strong evidence, high confidence, low risk.
    "Rewrite the scheduler" can have equally strong evidence and be the most
    dangerous item on the list. One blended number reports those as similar,
    which is precisely backwards for someone deciding what to start on Monday.

    ``alternatives`` carries the options that lost *with the reason each lost*.
    A recommendation without them is a suggestion; with them it is a decision
    somebody else can audit or overrule.
    """

    statement: str
    rationale: str
    confidence: Confidence
    tradeoffs: tuple[str, ...] = ()
    # Options considered and rejected. Typed rather than a list of strings so the
    # reason for rejection cannot be dropped.
    alternatives: tuple[Any, ...] = ()
    # How well the underlying facts are supported, distinct from how sure the
    # judgement is. Defaults to the recommendation's own confidence when the
    # caller has nothing better.
    evidence_strength: Confidence | None = None
    # How likely carrying this out is to go wrong. Higher is worse.
    execution_risk: Any = None
    effort: str = ""
    evidence: Evidence = field(default_factory=Evidence)
    # Which capability this work lands in, when it lands in one.
    initiative: str = ""

    @property
    def strength(self) -> Confidence:
        return self.evidence_strength or self.confidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "statement": self.statement,
            "rationale": self.rationale,
            "confidence": self.confidence.to_dict(),
            "evidence_strength": self.strength.to_dict(),
            "execution_risk": (
                self.execution_risk.to_dict() if self.execution_risk is not None else None
            ),
            "tradeoffs": list(self.tradeoffs),
            "alternatives": [a.to_dict() for a in self.alternatives],
            "effort": self.effort,
            "initiative": self.initiative,
            "evidence": self.evidence.to_dict(),
        }


class Mode(Enum):
    """
    Which register an answer is in.

    GROUNDED is the existing behaviour: answer what was asked, from what was
    retrieved. EXECUTIVE is the strategic register — the question is about what
    to do rather than what is true, and the answer owes the reader a decision.

    A mode is chosen per question, never per session. "Where is the responder
    implemented" deserves a file path even when the previous question was about
    roadmap strategy, and a session-wide switch would answer it with a memo.
    """

    GROUNDED = "grounded"
    EXECUTIVE = "executive"


@dataclass
class Assessment:
    """
    Everything the reasoning layer concluded about one question.

    This is the artefact that sits between retrieval and generation. It is built
    without a model and handed *to* one, which is the inversion the whole layer
    exists for: previously the model received documents and produced prose, so
    the reasoning happened somewhere unobservable and untestable. Now the
    reasoning is a value you can assert against, and the model's remaining job is
    to say it well.

    An empty assessment is a real and correct outcome — some questions are pure
    lookups. The responder checks `has_reasoning` rather than assuming.
    """

    question: str
    mode: Mode = Mode.GROUNDED
    subject: str = ""
    facts: list[Claim] = field(default_factory=list)
    inferences: list[Claim] = field(default_factory=list)
    recommendations: list[Recommendation] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)
    # The capabilities this project is building. Reasoning starts here and drills
    # down: "how is Billing going" is not a question about any file, and a layer
    # that can only answer at artefact level cannot answer it at all.
    initiatives: list[Any] = field(default_factory=list)
    # Why this question was routed to this mode, kept so a surprising register is
    # diagnosable rather than mysterious.
    mode_reason: str = ""
    # True when this answers a follow-up about a decision already made rather
    # than computing a new one. Same register and same budget as a fresh
    # executive turn -- what differs is that the ranking is not re-run, so the
    # flag travels rather than being inferred downstream.
    continuation: bool = False
    # The stored decision being continued, when there is one.
    prior: Any = None
    # Set when the project moved since the prior decision was computed. The
    # recommendation is still reported, but never as though it were current:
    # a stale recommendation presented as fresh is the failure mode that makes
    # a strategic assistant untrustworthy.
    stale: bool = False
    # What changed, when that is known. Empty is honest, not a bug.
    stale_because: str = ""
    # True when a capability the prior decision named is no longer discovered.
    obsolete: bool = False
    # True when this fresh assessment was run *because* a stored decision had
    # gone stale. Without it the answer reads as an unprompted new analysis, and
    # a model that has seen an earlier "Status: Current" line in the transcript
    # will cheerfully repeat it — asserting the project has not changed on the
    # very turn we reassessed because it had.
    replaced_stale: bool = False

    @property
    def has_reasoning(self) -> bool:
        return bool(self.inferences or self.recommendations or self.gaps or self.initiatives)

    @property
    def attention(self) -> list[Any]:
        """Initiatives that need a decision, worst first."""
        return [i for i in self.initiatives if i.health.needs_attention]

    @property
    def drift(self) -> list[Any]:
        """
        Where implementation evidence and the project record disagree.

        Surfaced separately from risks because it is a claim about the *record*
        rather than about the product: the capability may be perfectly healthy
        and the number describing it simply wrong. Conflating the two would send
        someone to fix code when the thing that needs fixing is a task status.
        """
        return [d for initiative in self.initiatives for d in initiative.drift]

    @property
    def overall(self) -> Confidence:
        """
        Confidence in the assessment as a whole.

        The *lowest* recommendation confidence dominates rather than the mean.
        Advice is acted on as a unit: if one of three recommendations rests on
        thin evidence, the reader's real risk is that one, and averaging it
        against two solid ones would hide precisely the thing they need to see.

        With no recommendations this falls back to the spread of facts, which
        measures how much was actually known rather than how well it was argued.
        """
        if self.recommendations:
            weakest = min(self.recommendations, key=lambda r: r.confidence.score)
            return Confidence(
                score=weakest.confidence.score,
                because=(
                    f"limited by the least-supported recommendation of {len(self.recommendations)}",
                    *weakest.confidence.because,
                ),
            )
        if self.facts:
            score = sum(f.confidence.score for f in self.facts) / len(self.facts)
            return Confidence(
                score=score,
                because=(f"mean support across {len(self.facts)} facts",),
            )
        return Confidence(score=0.0, because=("no evidence was retrieved",))

    def render(self) -> str:
        """
        The assessment as text for a responder to narrate.

        A continuation leads with the stored decision and its status, because a
        follow-up is about that decision and everything else is supporting
        material.

        Deliberately terse and labelled. This is not shown to a user — it is the
        structured material a model is asked to explain — so it optimises for
        being unambiguous about what is fact and what is not, rather than for
        reading well. Every heading here maps to a `ClaimKind`, so a model that
        simply follows the structure cannot silently promote an inference into a
        fact.
        """
        blocks: list[str] = []

        if self.replaced_stale:
            blocks.append(
                "# Reassessed\n"
                "A recommendation was made earlier in this conversation, and the project "
                "changed after it was computed. Because this question asks what to do now, "
                "that recommendation was NOT reused — the analysis below is fresh. Say so, "
                "and if the conclusion differs from the earlier one, name the difference. "
                "Do not claim the project is unchanged."
            )

        if self.prior is not None:
            lines = [self.prior.render()]
            if self.obsolete:
                lines.append(
                    "\nSTATUS: obsolete — a capability this recommendation named is no "
                    "longer present in the project. Report the recommendation as "
                    "superseded rather than as advice to act on."
                )
            elif self.stale:
                lines.append(
                    "\nSTATUS: stale — the project changed after this recommendation was "
                    "made. Report it as the prior recommendation, say plainly that the "
                    "project has moved since, and name what changed if it is given below. "
                    "Do not present it as current advice."
                    + (f"\nWhat changed: {self.stale_because}" if self.stale_because else "")
                )
            else:
                lines.append(
                    "\nSTATUS: current — the project has not changed since this was computed."
                )
            blocks.append("\n".join(lines))

        if self.initiatives:
            # Capabilities lead. A reader who stops after the first block should
            # still know how the product is doing, which is not recoverable from
            # a list of files.
            lines = ["# Capabilities (what this project is building)"]
            for initiative in self.initiatives:
                lines.append(f"- {initiative.summary_line()}")
            attention = self.attention
            if attention:
                lines.append(
                    "Needing attention: "
                    + "; ".join(f"{i.name} ({i.health.value})" for i in attention)
                )
            blocks.append("\n".join(lines))

        if self.drift:
            # Its own block, immediately after the roster. A stale record makes
            # every figure above it wrong, so a reader needs to know before they
            # act on any of them.
            lines = [
                "# Record/reality drift (the project record disagrees with the code)",
                "MondayOS has NOT changed any task. These are reported for a human to judge.",
            ]
            for item in self.drift:
                lines.append(item.render())
            blocks.append("\n".join(lines))

        if self.facts:
            lines = ["# Established facts (directly supported by this repository)"]
            lines += [f"- {c.statement}  [{c.derivation}]" for c in self.facts]
            blocks.append("\n".join(lines))

        if self.inferences:
            lines = ["# Inferences (concluded from those facts, not stated anywhere)"]
            for c in self.inferences:
                lines.append(f"- {c.statement}")
                lines.append(f"    derived by: {c.derivation}")
                lines.append(f"    confidence: {c.confidence.display()}")
            blocks.append("\n".join(lines))

        if self.gaps:
            lines = ["# Gaps (missing, with the work that would close them)"]
            for g in sorted(self.gaps, key=lambda x: x.severity):
                lines.append(f"- {g.subject}: {g.missing} — {g.why_it_matters}")
                lines.append(f"    would become: {g.suggested_task}")
            blocks.append("\n".join(lines))

        if self.recommendations:
            lines = ["# Recommendations (judgements, ranked)"]
            for i, r in enumerate(self.recommendations, 1):
                lines.append(f"{i}. {r.statement}")
                lines.append(f"    because: {r.rationale}")
                if r.initiative:
                    lines.append(f"    capability: {r.initiative}")
                if r.tradeoffs:
                    lines.append(f"    tradeoffs: {'; '.join(r.tradeoffs)}")
                for alt in r.alternatives:
                    lines.append(f"    alternative considered: {alt.statement}")
                    lines.append(f"        not chosen because: {alt.why_not}")
                if r.effort:
                    lines.append(f"    effort: {r.effort}")
                # Three separate scores. They disagree often, and the disagreement
                # is the most useful thing on the line.
                lines.append(f"    evidence strength: {r.strength.display()}")
                lines.append(f"    recommendation confidence: {r.confidence.display()}")
                if r.execution_risk is not None:
                    lines.append(f"    execution risk: {r.execution_risk.display()}")
                    lines.append(f"        risk because: {'; '.join(r.execution_risk.because)}")
            blocks.append("\n".join(lines))

        if blocks:
            overall = self.overall
            blocks.append(
                f"# Overall confidence\n{overall.display()} — {'; '.join(overall.because)}"
            )

        return "\n\n".join(blocks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "mode": self.mode.value,
            "mode_reason": self.mode_reason,
            "subject": self.subject,
            "facts": [c.to_dict() for c in self.facts],
            "inferences": [c.to_dict() for c in self.inferences],
            "recommendations": [r.to_dict() for r in self.recommendations],
            "gaps": [g.to_dict() for g in self.gaps],
            "initiatives": [i.to_dict() for i in self.initiatives],
            "drift": [d.to_dict() for d in self.drift],
            "overall_confidence": self.overall.to_dict(),
            "has_reasoning": self.has_reasoning,
            "continuation": self.continuation,
            "stale": self.stale,
            "stale_because": self.stale_because,
            "obsolete": self.obsolete,
            "replaced_stale": self.replaced_stale,
        }
