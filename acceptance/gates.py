"""
The acceptance gates.

Each is a property a program can check, scored through the shared `Evaluation`
primitive so that one rule holds everywhere: **a gate that evaluated nothing can
never report PASS.** Before this, gate 5 passed having compared no recommendation
keys, gate 12 passed having inspected no strategic state, and gate 9 passed while
reading a field no code ever populated. Each was a separate accident with the
same shape, which is why the fix is one primitive rather than six patches.

None of these judges whether an answer was *useful*. That is a human's job, and a
number standing in for it would be the least trustworthy figure in the report.

`Evaluation` lives here rather than in a module of its own because the gates are
its only caller, and because a MondayOS package gains a capability member for
every source file it holds -- a scoring helper used by nothing else should not
reshape the project's own measurements to exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Verdict(Enum):
    """
    What a gate concluded, and the four are genuinely distinct.

    PASS and FAIL are claims about the product. INCONCLUSIVE is a claim about the
    run — the property was not exercised, so nothing is known. UNVERIFIABLE is a
    claim about the environment — the property cannot be observed here at all,
    however many times it is attempted. Collapsing the last two would hide the
    difference between "we did not look" and "we cannot look".
    """

    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"
    UNVERIFIABLE = "unverifiable"


@dataclass
class Evaluation:
    """
    One gate's evidence and the verdict that follows from it.

    ``required`` is a coverage floor. A gate that compared one project of four
    has learned something real and not enough, and saying so is the difference
    between a report and a claim.
    """

    number: int
    name: str
    opportunities: int = 0
    exercised: int = 0
    passed: int = 0
    failures: list[str] = field(default_factory=list)
    # Set only when the property cannot be observed in this environment.
    unverifiable_because: str = ""
    # Minimum exercised observations for PASS. 0 means "any evidence will do",
    # which still excludes zero.
    required: int = 0
    note: str = ""

    def observe(self, ok: bool, because: str = "") -> None:
        """Record one evaluated observation."""
        self.exercised += 1
        if ok:
            self.passed += 1
        else:
            self.failures.append(because)

    def skip(self, count: int = 1) -> None:
        """Record chances that existed but could not be evaluated."""
        self.opportunities += count

    def offer(self, count: int = 1) -> None:
        """Record chances to evaluate, whether or not they are taken."""
        self.opportunities += count

    @property
    def failed(self) -> int:
        return len(self.failures)

    @property
    def unexercised(self) -> int:
        return max(0, self.opportunities - self.exercised)

    @property
    def verdict(self) -> Verdict:
        """
        Derived, in this order, and the order is the design.

        Unverifiable first: an environment that cannot show the property makes
        every other question moot. Failure next, because one real violation
        outranks any amount of coverage. Then the zero rule. Then coverage.
        """
        if self.unverifiable_because:
            return Verdict.UNVERIFIABLE
        if self.failures:
            return Verdict.FAIL
        if self.exercised == 0:
            return Verdict.INCONCLUSIVE
        if self.required and self.exercised < self.required:
            return Verdict.INCONCLUSIVE
        return Verdict.PASS

    @property
    def reason(self) -> str:
        """Why this verdict, in the terms a reader needs."""
        if self.unverifiable_because:
            return self.unverifiable_because
        if self.failures:
            return f"{self.failed} violation(s)"
        if self.exercised == 0:
            return (
                f"nothing was exercised ({self.opportunities} opportunit"
                f"{'y' if self.opportunities == 1 else 'ies'} existed)"
            )
        if self.required and self.exercised < self.required:
            return f"partial coverage: {self.exercised} of {self.required} required"
        return self.note or "—"

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": self.number,
            "name": self.name,
            "verdict": self.verdict.value,
            "opportunities": self.opportunities,
            "exercised": self.exercised,
            "passed": self.passed,
            "failed": self.failed,
            "unexercised": self.unexercised,
            "reason": self.reason,
            "violations": self.failures[:20],
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

# Turns whose whole purpose is to refer back to a decision. They are only
# meaningful once one exists.
_FOLLOW_UPS = ("C.", "D.", "I.say-more-warm")


def evaluate(
    turns: list[Any],
    projects: dict[str, Any],
    capabilities: dict[str, bool],
) -> list[Evaluation]:
    """
    Score every gate over a completed run.

    A turn whose failure was the provider's is excluded from scoring: it was
    already recorded as an incident, and counting it again would let someone
    else's outage read as a defect in this one. It is still counted as an
    *opportunity*, so the report shows what the run could not reach.
    """
    scored = [t for t in turns if t.outcome == "ok"]
    unscored = [t for t in turns if t.outcome != "ok"]
    available = [slug for slug, record in projects.items() if record.get("available")]
    gate = dict(GATES)

    def new(number: int, required: int = 0) -> Evaluation:
        return Evaluation(number=number, name=gate[number], required=required)

    results = [
        _citations_stay_in_project(new(1), scored, unscored),
        _no_invented_initiatives(new(2), scored, unscored),
        _lookups_stay_grounded(new(3), scored, unscored),
        _truncation_is_reported(new(4), scored, capabilities),
        _followups_preserve_recommendation(new(5), scored, projects),
        _quoted_scores_match(new(6), scored),
        _cold_elaboration_grounds(new(7), turns),
        _warm_elaboration_continues(new(8), turns, projects),
        _history_stays_in_project(new(9), scored, unscored),
        _no_invented_decisions(new(10), scored, unscored),
        _cited_lines_resolve(new(11), scored),
        _no_hidden_reasoning(new(12), projects),
        _every_corpus_completes(new(13), projects),
        _recommendations_are_deterministic(new(14, required=len(available)), projects),
    ]
    return results


# --------------------------------------------------------------------------- #
# evidence gates — one observation per turn that produced something to look at
# --------------------------------------------------------------------------- #


def _citations_stay_in_project(e: Evaluation, scored: list[Any], unscored: list[Any]) -> Evaluation:
    e.offer(len(scored) + len(unscored))
    for turn in scored:
        citations = turn.citations or {}
        if not citations.get("total"):
            continue
        outside = citations.get("outside_boundary") or []
        e.observe(not outside, f"{turn.project}/{turn.turn_id}: {outside}")
    e.note = f"{sum((t.citations or {}).get('total', 0) for t in scored)} citations checked"
    return e


def _no_invented_initiatives(e: Evaluation, scored: list[Any], unscored: list[Any]) -> Evaluation:
    e.offer(len(scored) + len(unscored))
    for turn in scored:
        named = (turn.initiatives or {}).get("named") or []
        if not named:
            continue
        invented = (turn.initiatives or {}).get("invented") or []
        e.observe(not invented, f"{turn.project}/{turn.turn_id}: {invented}")
    return e


def _lookups_stay_grounded(e: Evaluation, scored: list[Any], unscored: list[Any]) -> Evaluation:
    lookups = [t for t in scored if t.expect_register == "grounded"]
    e.offer(len([t for t in scored + unscored if t.expect_register == "grounded"]))
    for turn in lookups:
        if not turn.observed_register:
            continue
        e.observe(
            turn.observed_register == "grounded",
            f"{turn.project}/{turn.turn_id}: routed {turn.observed_register}",
        )
    return e


def _truncation_is_reported(
    e: Evaluation, scored: list[Any], capabilities: dict[str, bool]
) -> Evaluation:
    """
    Only answerable where the provider says why generation stopped.

    UNVERIFIABLE rather than skipped: a property that cannot be observed here is
    a different thing from one that simply was not, and a reader deciding on a
    release needs to know which.
    """
    if not capabilities.get("reports_stop_reason"):
        e.unverifiable_because = (
            "the configured provider does not expose a stop reason, so a truncated "
            "answer is indistinguishable from a finished one"
        )
        e.offer(len(scored))
        return e
    e.offer(len(scored))
    for turn in scored:
        if turn.stop_reason != "max_tokens":
            continue
        e.observe(
            turn.incomplete, f"{turn.project}/{turn.turn_id}: truncated but reported complete"
        )
    if e.exercised == 0:
        e.note = "no answer hit the token ceiling, so the flag was never exercised"
    return e


def _quoted_scores_match(e: Evaluation, scored: list[Any]) -> Evaluation:
    """
    A number the answer states must be one MondayOS computed.

    Checked against **every** score in the assessment -- facts, inferences,
    recommendations and rejected alternatives -- rather than against the winning
    recommendation's three. An answer may legitimately quote the confidence of an
    inference it is explaining, and comparing that to `recommendations[0]` made a
    faithful recitation look like an invention: sourcingBOT's assessment computed
    eight distinct values and the harness knew one of them.

    A turn whose assessment computed nothing is not compared at all. There is no
    such thing as disagreeing with a number that was never produced, and grounded
    turns have no recommendation to disagree with (RC1/H-7).
    """
    e.offer(len(scored))
    for turn in scored:
        computed = [float(v) for v in (turn.computed_values or [])]
        if not computed or not turn.quoted_scores:
            continue
        for name, stated in turn.quoted_scores.items():
            e.observe(
                any(abs(stated - value) <= 0.05 for value in computed),
                f"{turn.project}/{turn.turn_id}: said {name}={stated}, "
                f"which matches no computed value in {computed}",
            )
    if e.exercised == 0:
        e.note = "no answer quoted a score against an assessment that computed any"
    return e


def _history_stays_in_project(e: Evaluation, scored: list[Any], unscored: list[Any]) -> Evaluation:
    """
    Commits an answer cited, checked against the project's own history.

    This gate previously read `TurnRecord.foreign_history`, which no code ever
    wrote to. It was structurally incapable of failing and reported PASS on every
    run. It now scores only turns where commit references were actually observed.
    """
    e.offer(len(scored) + len(unscored))
    for turn in scored:
        if not turn.cited_commits:
            continue
        e.observe(
            not turn.foreign_history,
            f"{turn.project}/{turn.turn_id}: cites {turn.foreign_history}",
        )
    e.note = f"{sum(len(t.cited_commits) for t in scored)} commit references checked"
    return e


def _no_invented_decisions(e: Evaluation, scored: list[Any], unscored: list[Any]) -> Evaluation:
    e.offer(len(scored) + len(unscored))
    for turn in scored:
        if not turn.cited_decisions:
            continue
        e.observe(
            not turn.invented_decisions,
            f"{turn.project}/{turn.turn_id}: {turn.invented_decisions}",
        )
    return e


def _cited_lines_resolve(e: Evaluation, scored: list[Any]) -> Evaluation:
    e.offer(len(scored))
    for turn in scored:
        citations = turn.citations or {}
        if not citations.get("with_line"):
            continue
        invalid = citations.get("invalid_lines") or []
        e.observe(not invalid, f"{turn.project}/{turn.turn_id}: {invalid}")
    return e


# --------------------------------------------------------------------------- #
# state gates — meaningful only once a decision the user saw actually exists
# --------------------------------------------------------------------------- #


def _followups_preserve_recommendation(
    e: Evaluation, scored: list[Any], projects: dict[str, Any]
) -> Evaluation:
    """
    A follow-up must not silently re-decide.

    Scored only against an anchor from a *completed* strategic turn. Anchoring on
    a recommendation computed before generation — as this once did — blames the
    product for refusing to continue a decision the user never saw.
    """
    for project, record in projects.items():
        if not record.get("available"):
            continue
        anchor = record.get("recommendation_key", "")
        followups = [
            t for t in scored if t.project == project and t.turn_id.startswith(_FOLLOW_UPS)
        ]
        e.offer(len(followups))
        if not anchor:
            continue
        for turn in followups:
            if not turn.recommendation_key:
                # A grounded follow-up carries no key. Nothing to compare, and
                # nothing to conclude from its absence.
                continue
            e.observe(
                turn.recommendation_key == anchor or turn.reassessed,
                f"{project}/{turn.turn_id}: {anchor!r} became {turn.recommendation_key!r}",
            )
    if e.exercised == 0:
        e.note = "no completed strategic turn produced an anchor to compare against"
    return e


def _cold_elaboration_grounds(e: Evaluation, turns: list[Any]) -> Evaluation:
    cold = [t for t in turns if t.turn_id.endswith("say-more-cold")]
    e.offer(len(cold))
    for turn in cold:
        if turn.outcome != "ok" or not turn.observed_register:
            continue
        e.observe(
            turn.observed_register == "grounded",
            f"{turn.project}: routed {turn.observed_register} with nothing to continue",
        )
    return e


def _warm_elaboration_continues(
    e: Evaluation, turns: list[Any], projects: dict[str, Any]
) -> Evaluation:
    """
    Evaluated only where a strategic turn completed and its state was persisted.

    Without persisted state there is nothing to continue, and MondayOS grounding
    the turn is its documented contract rather than a defect.
    """
    warm = [t for t in turns if t.turn_id.endswith("say-more-warm")]
    e.offer(len(warm))
    for turn in warm:
        record = projects.get(turn.project) or {}
        if not record.get("strategy_persisted"):
            continue
        if turn.outcome != "ok" or not turn.observed_register:
            continue
        e.observe(
            turn.observed_register in ("continuation", "executive"),
            f"{turn.project}: persisted state exists but the turn routed {turn.observed_register}",
        )
    if e.exercised == 0:
        e.note = "no project persisted strategic state, so there was nothing to continue"
    return e


def _no_hidden_reasoning(e: Evaluation, projects: dict[str, Any]) -> Evaluation:
    """
    Persisted strategic state may hold only what the user saw.

    Scored per project that actually persisted state. A run where nothing was
    persisted inspected nothing and must say so.
    """
    for project, record in projects.items():
        if not record.get("available"):
            continue
        e.offer()
        if not record.get("strategy_persisted"):
            continue
        hidden = record.get("hidden_reasoning_keys") or []
        e.observe(not hidden, f"{project}: persisted {hidden}")
    if e.exercised == 0:
        e.note = "no strategic state was persisted anywhere, so none could be inspected"
    return e


def _every_corpus_completes(e: Evaluation, projects: dict[str, Any]) -> Evaluation:
    """
    No turn was abandoned for want of conversational state.

    Deliberately not "every turn answered": a provider outage is not a product
    failure. What this asserts is that MondayOS never asked for clarification it
    should already have had.
    """
    for project, record in projects.items():
        if not record.get("available"):
            continue
        e.offer()
        e.observe(
            bool(record.get("completed")),
            f"{project}: {record.get('incomplete_reason', 'did not finish')}",
        )
    return e


def _recommendations_are_deterministic(e: Evaluation, projects: dict[str, Any]) -> Evaluation:
    """
    The same question against an unchanged project yields the same decision.

    ``required`` is the number of available projects, so a repeat exercised on
    one of four is partial coverage rather than a pass.
    """
    for project, record in projects.items():
        if not record.get("available"):
            continue
        e.offer()
        repeat = record.get("determinism") or {}
        if not repeat:
            continue
        mismatched = [
            f"{project}: {field} {first!r} -> {second!r}"
            for field, (first, second) in repeat.items()
            if first != second
        ]
        e.observe(not mismatched, "; ".join(mismatched))
    return e


def overall(results: list[Evaluation]) -> str:
    """READY, READY WITH KNOWN LIMITATIONS, or NOT READY."""
    if any(r.verdict is Verdict.FAIL for r in results):
        return "NOT READY"
    if any(r.verdict in (Verdict.INCONCLUSIVE, Verdict.UNVERIFIABLE) for r in results):
        return "READY WITH KNOWN LIMITATIONS"
    return "READY"
