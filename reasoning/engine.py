"""
The reasoning engine — retrieval's output in, an assessment out.

This is the layer the whole package exists to insert:

    question -> retrieve -> **understand -> reason -> recommend -> score** -> narrate

Everything to the left of the bold section already existed and worked well. What
was missing was any component whose job was to conclude something, so the model
received documents and did the only thing possible with documents: summarised
them. Moving that work into a testable, deterministic component is what turns a
tendency into a capability — a prompt that produces strategic answers today drifts
when the model changes, whereas this produces the same assessment from the same
repository every time, and a test can say so.

**Facts are gathered once per engine, not once per question.** They describe the
project, not the query, and re-walking the index on every turn would make
conversation cost grow with repository size for no benefit. Inference and
recommendation, which do depend on the question, run per call.

The engine is scoped to one project by construction: it holds one index and one
graph, both already project-scoped, so there is no argument that could widen it.
That is the same isolation rule the Context Engine holds (ADR-017), inherited
rather than reimplemented.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import initiatives as initiative_intelligence
from intelligence.graph import RelationshipGraph
from intelligence.index import ProjectIndex
from reasoning import gaps as gap_analysis
from reasoning import recommend
from reasoning.executive import Topic
from reasoning.facts import ProjectFacts, gather
from reasoning.inference import infer
from reasoning.models import Assessment, Mode
from reasoning.router import ROUTER

# How many facts and inferences reach the narrator. The assessment is injected
# into a prompt with a finite budget, and twenty inferences would crowd out the
# retrieved context the answer is actually about.
FACT_LIMIT = 6
INFERENCE_LIMIT = 5
GAP_LIMIT = 5
# Capabilities shown to the narrator. A roster longer than this stops being a
# product view and becomes a directory listing, which is the thing it replaces.
INITIATIVE_LIMIT = 12


def _changed_since(state: Any, named: list[Any]) -> str:
    """
    What moved, in terms the reader can check.

    Deliberately narrow: health and blocker counts for the capabilities the prior
    decision actually named. A full diff of the project would be accurate and
    unreadable, and the question being answered is "does the prior advice still
    hold", not "what happened".
    """
    notes: list[str] = []
    for initiative in named:
        if initiative.health.needs_attention:
            notes.append(f"{initiative.name} is now {initiative.health.value}")
        if initiative.blockers:
            notes.append(f"{initiative.name} has {len(initiative.blockers)} blocker(s)")
        for drift in initiative.drift:
            notes.append(drift.statement)
    return "; ".join(notes[:4])


class ReasoningEngine:
    """
    Reasons about one project.

    Constructed with the artefacts retrieval already built — this never indexes,
    never walks a repository, and never writes anything. It is pure analysis over
    values it was handed, which is what makes it cheap to test and impossible to
    have side effects.
    """

    def __init__(
        self,
        index: ProjectIndex,
        graph: RelationshipGraph,
        tasks: list[dict[str, Any]] | None = None,
        config_dir: Path | None = None,
    ) -> None:
        self._index = index
        self._graph = graph
        self._tasks = tasks or []
        # Where declared initiatives live. Optional: without it MondayOS reasons
        # about what exists, which is a working roster and not a broken one — but
        # it cannot see a capability that was planned and never started.
        self._config_dir = config_dir
        self._facts: ProjectFacts | None = None
        self._gaps: list[Any] | None = None
        self._initiatives: list[Any] | None = None

    @property
    def project(self) -> str:
        return self._index.project

    def facts(self) -> ProjectFacts:
        """Project facts, gathered once and reused."""
        if self._facts is None:
            self._facts = gather(self._index, self._graph, self._tasks)
        return self._facts

    def gaps(self) -> list[Any]:
        if self._gaps is None:
            self._gaps = gap_analysis.find(self.facts(), self._index)
        return self._gaps

    def initiatives(self) -> list[Any]:
        """
        The capabilities this project is building, assessed and linked.

        Cached like facts: membership describes the project, not the question.
        A failure here costs the capability view and nothing else — reasoning
        falls back to the artefact level, which is where it was before.
        """
        if self._initiatives is None:
            try:
                self._initiatives = initiative_intelligence.build(
                    self._index, self._graph, self._tasks, config_dir=self._config_dir
                )
            except Exception:  # noqa: BLE001 — a partial view beats no answer
                self._initiatives = []
        return self._initiatives

    def continue_from(
        self,
        state: Any,
        question: str,
        fingerprint: str = "",
    ) -> Assessment:
        """
        Answer a follow-up about a decision already made.

        **The ranking is not re-run.** The stored winner and its alternatives are
        the answer to "what did you recommend"; recomputing them would risk
        producing a different winner and then discussing it as though it were the
        one the user is asking about — which is worse than not answering.

        Current state is consulted only to say whether the decision still holds:
        whether the project moved, and whether the capabilities it named still
        exist. That is reporting, not re-deciding.

        A follow-up asking what to do *now* against a project that has since
        changed never reaches here: the router sends it for a fresh assessment
        instead. Describing a past recommendation is always safe; acting on an
        outdated one is how a system gives confidently obsolete advice, and that
        judgement belongs with the routing decision rather than being made twice.
        """
        # Staleness is the router's decision -- it is a property of the
        # conversation, not of the project this engine reasons about. Recomputing
        # it here would be a second opinion nobody asked for.
        stale = bool(fingerprint and state.fingerprint and fingerprint != state.fingerprint)

        capabilities = self.initiatives()
        by_slug = {i.slug: i for i in capabilities}
        named = [by_slug[s] for s in state.initiative_slugs if s in by_slug]
        missing = [s for s in state.initiative_slugs if s not in by_slug]

        assessment = Assessment(
            question=question,
            mode=Mode.EXECUTIVE,
            subject=state.initiative_slug,
            mode_reason="continues the prior strategic assessment",
            continuation=True,
            prior=state,
            stale=stale,
            obsolete=bool(missing),
            initiatives=named[:INITIATIVE_LIMIT],
            facts=self.facts().claims[:FACT_LIMIT],
        )

        if missing:
            assessment.stale_because = (
                f"{', '.join(missing)} is no longer discovered in this project"
            )
        elif stale:
            assessment.stale_because = _changed_since(state, named)

        return assessment

    def assess(
        self,
        question: str,
        subject: str = "",
        thin_retrieval: bool = False,
        route: Any = None,
    ) -> Assessment:
        """
        Reason about one question.

        ``thin_retrieval`` is the caller's report that the context snapshot came
        back with little in it. It is what replaces "the context does not contain
        that" with an answer: when retrieval is thin, the assessment carries
        inferences and gaps so the responder has something real to reason from,
        rather than nothing and an apology.

        A strategic question gets the full treatment regardless, because for
        "what should we build next" there is no retrieval result that would make
        reasoning unnecessary.
        """
        # The decision arrives from the router. It used to be re-derived here,
        # which meant a second routing pass that could not see the conversation's
        # strategic state -- two policies, one of them structurally blind.
        decision = route if route is not None else ROUTER.route(question)
        facts = self.facts()

        if decision.executive and decision.topic is not None:
            return self._executive(question, subject, decision, decision.topic)

        assessment = Assessment(
            question=question,
            mode=Mode.GROUNDED,
            subject=subject,
            mode_reason=decision.reason,
            facts=facts.claims[:FACT_LIMIT],
        )

        # A well-retrieved lookup wants a file path, not a memo. Inferences are
        # attached only when retrieval was thin — precisely the case that used to
        # produce "the context does not contain...".
        if thin_retrieval:
            assessment.inferences = infer(facts)[:INFERENCE_LIMIT]
            assessment.gaps = self.gaps()[:2]
            assessment.initiatives = self.initiatives()[:6]
            assessment.mode_reason = f"{decision.reason}; retrieval was thin"

        return assessment

    def _executive(
        self,
        question: str,
        subject: str,
        routing: Any,
        topic: Topic,
    ) -> Assessment:
        facts = self.facts()
        inferences = infer(facts)
        found = self.gaps()
        capabilities = self.initiatives()

        return Assessment(
            question=question,
            mode=Mode.EXECUTIVE,
            subject=subject,
            mode_reason=routing.reason,
            facts=facts.claims[:FACT_LIMIT],
            inferences=inferences[:INFERENCE_LIMIT],
            gaps=found[:GAP_LIMIT],
            # Capabilities lead the assessment. Artefact-level findings are the
            # drill-down, not the headline.
            initiatives=capabilities[:INITIATIVE_LIMIT],
            recommendations=recommend.build(
                topic, facts, inferences, found, initiatives=capabilities
            ),
        )
