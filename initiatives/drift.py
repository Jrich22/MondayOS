"""
Detecting disagreement between what was built and what the record says.

MondayOS reads two different kinds of source about the same work. Implementation
evidence — code, tests, commits, merged pull requests — records what actually
happened. The task system records what someone *said* would happen. They are
maintained by different acts at different moments, so they come apart, and when
they do every number computed from either one is quietly wrong.

The case that prompted this module: Growth BOT reports 0% complete against 8
tasks, while its code is present, tested, and named in merged pull requests. The
percentage is faithful to the task store and wrong about the world.

**Nothing here repairs anything.** Detection and repair are separate decisions and
only one of them is safe to make automatically. Closing a task because commits
exist would be MondayOS overwriting a human's record on the strength of a
heuristic, and the first time it guessed wrong it would erase the fact that
somebody deliberately reopened something. So drift is reported, attributed, and
left for a person — the same rule gap analysis follows for proposed work.

Detection is deliberately narrow. It fires only where the two sources make
directly contradictory claims, not merely where one is thin: an initiative with no
tasks is under-recorded, which is a different observation and not this one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from initiatives.models import Initiative, Member
from intelligence.models import NodeKind

# Merged pull requests or commits naming an initiative, at or above which the
# implementation evidence is strong enough to contradict a backlog status. One
# passing mention in a commit message is not a shipped capability.
SHIPPED_EVIDENCE = 2


class DriftKind(Enum):
    """
    Which direction the two records disagree in.

    The direction matters for what a human should do about it, which is why this
    is not a single "inconsistent" flag: one means the backlog is stale, the other
    means the backlog is optimistic, and those call for opposite corrections.
    """

    # Code shipped; the task system still calls it backlog. The record is stale.
    SHIPPED_BUT_BACKLOG = "shipped-but-backlog"
    # Tasks closed; no implementation evidence exists. The record is optimistic,
    # or the work landed somewhere MondayOS cannot see.
    COMPLETE_BUT_ABSENT = "complete-but-absent"
    # A task says blocked while commits continue to land against the capability.
    BLOCKED_BUT_MOVING = "blocked-but-moving"


@dataclass
class Drift:
    """
    One disagreement between implementation evidence and the project record.

    ``implementation`` and ``record`` state each side in its own terms rather than
    resolving them, because the whole point is that MondayOS does not know which
    is right. Presenting a verdict would be the automatic repair this module
    deliberately does not perform.
    """

    kind: DriftKind
    initiative: str
    implementation: str
    record: str
    consequence: str
    evidence: list[Member] = field(default_factory=list)

    @property
    def statement(self) -> str:
        return (
            f"{self.initiative}: implementation evidence says {self.implementation}, "
            f"but the task system says {self.record}"
        )

    def render(self) -> str:
        return f"- {self.statement}\n    therefore: {self.consequence}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "initiative": self.initiative,
            "implementation": self.implementation,
            "record": self.record,
            "consequence": self.consequence,
            "statement": self.statement,
            "evidence": [m.to_dict() for m in self.evidence],
            # Stated on every instance so no consumer has to infer it. Reporting
            # is safe; repairing is a human decision this increment does not take.
            "repaired": False,
        }


def _status_of(label: str) -> str:
    if "[" in label and "]" in label:
        return label[label.index("[") + 1 : label.index("]")].strip().lower()
    return ""


def detect(initiative: Initiative) -> list[Drift]:
    """
    Disagreements between what was built and what the record claims.

    Returns an empty list for the overwhelmingly common case where the two agree,
    or where there is not enough of either to disagree.
    """
    out: list[Drift] = []

    tasks = initiative.of_kind(NodeKind.TASK)
    statuses = [_status_of(m.label) for m in tasks]
    backlog = [
        m for m, s in zip(tasks, statuses, strict=False) if s in ("backlog", "todo", "open", "")
    ]
    completed = [
        m for m, s in zip(tasks, statuses, strict=False) if s in ("completed", "done", "closed")
    ]
    blocked = [m for m, s in zip(tasks, statuses, strict=False) if "block" in s]

    history = initiative.of_kind(NodeKind.COMMIT) + initiative.of_kind(NodeKind.PULL_REQUEST)
    shipped = (
        initiative.progress.has_code
        and initiative.progress.has_tests
        and len(history) >= SHIPPED_EVIDENCE
    )

    if shipped and tasks and not completed and backlog:
        out.append(
            Drift(
                kind=DriftKind.SHIPPED_BUT_BACKLOG,
                initiative=initiative.name,
                implementation=(
                    f"shipped — code and tests are present and "
                    f"{len(history)} commit(s)/PR(s) name it"
                ),
                record=f"all {len(backlog)} task(s) are still backlog",
                consequence=(
                    "the recorded progress for this capability is understated, and any "
                    "roadmap figure derived from it is too"
                ),
                evidence=history[:4] + backlog[:4],
            )
        )

    if completed and not initiative.progress.has_code and not history:
        out.append(
            Drift(
                kind=DriftKind.COMPLETE_BUT_ABSENT,
                initiative=initiative.name,
                implementation="no code, tests, commits or pull requests found",
                record=f"{len(completed)} task(s) marked complete",
                consequence=(
                    "either the work landed somewhere MondayOS cannot see, or the "
                    "record is optimistic — the capability cannot be verified from here"
                ),
                evidence=completed[:4],
            )
        )

    if blocked and len(history) >= SHIPPED_EVIDENCE:
        out.append(
            Drift(
                kind=DriftKind.BLOCKED_BUT_MOVING,
                initiative=initiative.name,
                implementation=f"{len(history)} commit(s)/PR(s) name this capability",
                record=f"{len(blocked)} task(s) marked blocked",
                consequence=(
                    "either the blocker cleared without the task being updated, or work "
                    "is proceeding around it"
                ),
                evidence=history[:3] + blocked[:3],
            )
        )

    return out


def detect_all(found: list[Initiative]) -> list[Drift]:
    """Drift across every initiative, most contradictory first."""
    out: list[Drift] = []
    for initiative in found:
        drifts = detect(initiative)
        initiative.drift = drifts
        out.extend(drifts)
    # Stale records outrank the softer disagreements: an understated roadmap is
    # acted on directly, whereas a blocked-but-moving task is usually noise.
    order = {
        DriftKind.SHIPPED_BUT_BACKLOG: 0,
        DriftKind.COMPLETE_BUT_ABSENT: 1,
        DriftKind.BLOCKED_BUT_MOVING: 2,
    }
    out.sort(key=lambda d: (order.get(d.kind, 9), d.initiative))
    return out
