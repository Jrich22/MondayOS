"""
The Initiative — a business capability, assembled from the work that builds it.

Everything MondayOS modelled until now was an artefact: a file, a task, a commit,
a decision. Those are what engineers touch. None of them is what a product is
made of, and no amount of reasoning over them answers "how is Billing going?" —
because Billing is not a file, and the question is not about any of its parts.

An initiative is the missing noun. It groups tasks, code, decisions, tests and
history into the capability they exist to deliver, so reasoning can start at the
level a founder actually thinks at and drill down only when asked.

**An initiative may have no code at all.** That is the design, not a degenerate
case. A repository-derived view can only ever see what has been built, so it can
never say "we committed to Billing and have not started" — the most important
sentence in most roadmap conversations. Declared initiatives are what let Monday
reason about a roadmap rather than an inventory, and `declared` is carried on the
type so the difference is never guessed.

Membership always carries its reason. An initiative that cannot say why a file
belongs to it is a cluster, and a cluster is something nobody can correct.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from core.identity import slug
from intelligence.models import NodeKind


class Health(Enum):
    """
    How an initiative is doing, in the coarsest terms that still decide anything.

    Ordered worst to best. The bands exist because a percentage answers "how much
    is done" and not "should I worry", and the second question is the one asked in
    every roadmap review.
    """

    BLOCKED = "blocked"
    AT_RISK = "at-risk"
    STALLED = "stalled"
    HEALTHY = "healthy"
    NOT_STARTED = "not-started"

    @property
    def needs_attention(self) -> bool:
        return self in (Health.BLOCKED, Health.AT_RISK, Health.STALLED)


class Basis(Enum):
    """
    What a progress figure was computed from.

    Carried because the two bases are not comparable and presenting them as one
    number would be a lie of exactly the kind this system tries not to tell.
    """

    # Completed tasks against total tasks. A real ratio.
    TASKS = "tasks"
    # Presence of code, tests, documentation and a decision record. A maturity
    # signal, deliberately not expressed as a percentage.
    SIGNALS = "signals"
    # Nothing to measure yet.
    NONE = "none"


@dataclass(frozen=True)
class Member:
    """
    One artefact belonging to an initiative, and why it belongs.

    ``because`` is the whole difference between a grouping a human can correct and
    one they have to accept. "task title begins 'Cue App:'" can be argued with;
    membership with no stated reason cannot.
    """

    node_id: str
    kind: NodeKind
    label: str
    because: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "kind": self.kind.value,
            "label": self.label,
            "because": self.because,
        }


@dataclass
class Progress:
    """
    How far along an initiative is, and on what basis.

    ``percent`` is None whenever no honest percentage exists. An initiative with
    no tasks has no denominator, and inventing one — counting files, say — would
    produce a number that moves when someone splits a module in two. A missing
    percentage with a stated maturity is more useful than a confident wrong one.
    """

    basis: Basis = Basis.NONE
    completed: int = 0
    total: int = 0
    # Maturity signals, used when there are no tasks to count.
    has_code: bool = False
    has_tests: bool = False
    has_docs: bool = False
    has_decision: bool = False

    @property
    def percent(self) -> int | None:
        if self.basis is Basis.TASKS and self.total:
            return int(round(100 * self.completed / self.total))
        return None

    @property
    def signal_count(self) -> int:
        return sum((self.has_code, self.has_tests, self.has_docs, self.has_decision))

    def display(self) -> str:
        if self.basis is Basis.TASKS and self.total:
            return f"{self.percent}% ({self.completed}/{self.total} tasks)"
        if self.basis is Basis.SIGNALS:
            present = [
                name
                for name, flag in (
                    ("code", self.has_code),
                    ("tests", self.has_tests),
                    ("docs", self.has_docs),
                    ("decision", self.has_decision),
                )
                if flag
            ]
            return f"{self.signal_count}/4 maturity signals ({', '.join(present) or 'none'})"
        return "not started"

    def to_dict(self) -> dict[str, Any]:
        return {
            "basis": self.basis.value,
            "completed": self.completed,
            "total": self.total,
            "percent": self.percent,
            "signals": {
                "code": self.has_code,
                "tests": self.has_tests,
                "docs": self.has_docs,
                "decision": self.has_decision,
                "count": self.signal_count,
            },
            "display": self.display(),
        }


@dataclass(frozen=True)
class Dependency:
    """
    One initiative relying on another, with the artefact that proves it.

    Derived only from stated relationships — a shared file, a decision one cites
    and the other owns. Never from similarity, because an inferred dependency is a
    constraint the project never agreed to and would be acted on as if it had.
    """

    on: str
    because: str

    def to_dict(self) -> dict[str, Any]:
        return {"on": self.on, "because": self.because}


@dataclass(frozen=True)
class Milestone:
    """The next meaningful step, with what makes it the next one."""

    statement: str
    rationale: str
    unblocks: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "statement": self.statement,
            "rationale": self.rationale,
            "unblocks": list(self.unblocks),
        }


@dataclass
class Seed:
    """
    A candidate initiative before its members are gathered.

    Lives here rather than in `discover` because both declaration and discovery
    produce them. A declared seed and a derived seed are the same kind of thing
    arriving from different directions, and putting the type in one of the two
    producers would make the other depend on it for no reason.
    """

    name: str
    because: str
    declared: bool = False
    # Path prefixes this initiative owns outright.
    paths: list[str] = field(default_factory=list)
    # Phrases whose presence indicates membership.
    keywords: list[str] = field(default_factory=list)
    summary: str = ""
    # Ranking for name selection when seeds merge; higher wins.
    authority: int = 0
    # Source files under this seed's paths, for package seeds.
    weight: int = 0
    # Set when a stronger signal absorbed a weaker one naming the same thing. A
    # package that also has a document or a task prefix is a capability; a
    # package alone is usually just a module.
    corroborated: bool = False
    # Whether concrete work stands behind this seed -- a directory, a name
    # recurring across layers, or a task prefix. A document alone does not
    # qualify: `RESEARCH_ROADMAP.md` is somebody planning research, and reporting
    # it as a capability is how a plan title ends up on the roster.
    work: bool = False

    @property
    def slug(self) -> str:
        return slugify(self.name)


@dataclass
class Initiative:
    """
    One business capability and everything MondayOS knows about it.

    The assessment fields are filled by `initiatives.health`, not here: this type
    holds what an initiative *is*, and computing how it is going is a separate
    concern that changes for different reasons.
    """

    slug: str
    name: str
    # True when a human named this initiative rather than MondayOS deriving it.
    # A declared initiative can legitimately have nothing in it — that is a
    # roadmap commitment, and reporting it as empty is the point.
    declared: bool = False
    summary: str = ""
    members: list[Member] = field(default_factory=list)
    # Why MondayOS believes this initiative exists at all.
    because: str = ""

    # ------------------------------------------------------------- assessment
    progress: Progress = field(default_factory=Progress)
    health: Health = Health.NOT_STARTED
    health_because: str = ""
    blockers: list[str] = field(default_factory=list)
    dependencies: list[Dependency] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    opportunities: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    next_milestone: Milestone | None = None
    # Disagreements between implementation evidence and the project record.
    # Reported, never repaired: closing a task because commits exist would be
    # MondayOS overwriting a human's record on a heuristic.
    drift: list[Any] = field(default_factory=list)

    def of_kind(self, kind: NodeKind) -> list[Member]:
        return [m for m in self.members if m.kind is kind]

    @property
    def empty(self) -> bool:
        return not self.members

    def summary_line(self) -> str:
        """One line for a roster, leading with the thing that decides attention."""
        return f"{self.name}: {self.health.value}, {self.progress.display()}" + (
            f", {len(self.blockers)} blocker(s)" if self.blockers else ""
        )

    def render(self) -> str:
        """The initiative as text for a responder to narrate."""
        lines = [f"## {self.name}"]
        if self.summary:
            lines.append(self.summary)
        lines.append(f"- health: {self.health.value} — {self.health_because}")
        lines.append(f"- progress: {self.progress.display()}")
        if self.declared and self.empty:
            lines.append("- declared on the roadmap; no implementing work found yet")
        if self.blockers:
            lines.append(f"- blockers: {'; '.join(self.blockers[:4])}")
        if self.dependencies:
            lines.append(
                "- depends on: " + "; ".join(f"{d.on} ({d.because})" for d in self.dependencies[:4])
            )
        if self.risks:
            lines.append(f"- risks: {'; '.join(self.risks[:3])}")
        if self.missing:
            lines.append(f"- missing: {'; '.join(self.missing[:3])}")
        if self.opportunities:
            lines.append(f"- opportunities: {'; '.join(self.opportunities[:3])}")
        if self.drift:
            lines.append("- record/reality drift:")
            for item in self.drift:
                lines.append(f"    {item.statement}")
                lines.append(f"        therefore: {item.consequence}")
        if self.next_milestone is not None:
            lines.append(
                f"- next milestone: {self.next_milestone.statement} "
                f"({self.next_milestone.rationale})"
            )
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "declared": self.declared,
            "summary": self.summary,
            "because": self.because,
            "member_count": len(self.members),
            "members": [m.to_dict() for m in self.members],
            "progress": self.progress.to_dict(),
            "health": self.health.value,
            "health_because": self.health_because,
            "blockers": list(self.blockers),
            "dependencies": [d.to_dict() for d in self.dependencies],
            "risks": list(self.risks),
            "opportunities": list(self.opportunities),
            "missing": list(self.missing),
            "next_milestone": (
                self.next_milestone.to_dict() if self.next_milestone is not None else None
            ),
            "drift": [d.to_dict() for d in self.drift],
        }


def slugify(name: str) -> str:
    """
    A stable id for an initiative name.

    Uses the canonical transformation without path validation: an initiative slug
    is a logical identifier -- a dictionary key and a cross-reference -- and never
    becomes a directory. Validating it as a path would reject capability names
    that are perfectly good identifiers.
    """
    return slug(name)
