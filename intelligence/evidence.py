"""
Citations — what an answer is based on, in a form you can open.

The rule this module exists to enforce: **an answer states its sources, and every
source is navigable.** "workspace/service.py" is a hint; "workspace/service.py
lines 82-140" is checkable. The difference decides whether the evidence trail is
useful or decorative, because a citation nobody can follow is one nobody
verifies, and one nobody verifies is one that can quietly be wrong.

Citations carry their own kind so a caller can render them as links without
parsing strings: a file citation opens an editor, an ADR citation scrolls a
decision log, a PR citation opens a browser. The formatting here is the fallback
for surfaces that cannot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CitationKind(Enum):
    FILE = "file"
    SYMBOL = "symbol"
    DECISION = "decision"
    TASK = "task"
    COMMIT = "commit"
    PULL_REQUEST = "pull-request"
    TEST = "test"
    KNOWLEDGE = "knowledge"


@dataclass(frozen=True)
class Citation:
    """One navigable source."""

    kind: CitationKind
    reference: str
    label: str = ""
    path: str = ""
    line: int = 0
    end_line: int = 0
    # Why this source was retrieved. Distinct from the answer: this explains the
    # *retrieval*, so a reader can tell a keyword hit from a graph relationship.
    because: str = ""

    def display(self) -> str:
        """A single readable line, for surfaces that cannot render links."""
        if self.path and self.line:
            span = (
                f"lines {self.line}-{self.end_line}"
                if self.end_line > self.line
                else f"line {self.line}"
            )
            return f"{self.path} {span}"
        return self.path or self.reference

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "reference": self.reference,
            "label": self.label,
            "path": self.path,
            "line": self.line,
            "end_line": self.end_line,
            "because": self.because,
            "display": self.display(),
        }


@dataclass
class Evidence:
    """
    Everything an answer was built from, grouped by kind.

    Order within a group is the retrieval order, which is deterministic — so the
    same question against the same project cites the same sources in the same
    sequence, and a diff between two answers is meaningful.
    """

    citations: list[Citation] = field(default_factory=list)

    def add(self, citation: Citation) -> None:
        # Deduplicate on identity, keeping the first reason. A file reached by
        # two routes is one source, and listing it twice would overstate how much
        # the answer rests on it.
        key = (citation.kind, citation.reference, citation.line)
        if any((c.kind, c.reference, c.line) == key for c in self.citations):
            return
        self.citations.append(citation)

    def of(self, kind: CitationKind) -> list[Citation]:
        return [c for c in self.citations if c.kind is kind]

    @property
    def empty(self) -> bool:
        return not self.citations

    def summary(self) -> str:
        """The "Based on" line."""
        if self.empty:
            return "Based on: nothing — the project index has no matching source."
        groups: dict[str, int] = {}
        for citation in self.citations:
            groups[citation.kind.value] = groups.get(citation.kind.value, 0) + 1
        parts = [f"{count} {kind}" for kind, count in sorted(groups.items())]
        return "Based on: " + ", ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary(),
            "citations": [c.to_dict() for c in self.citations],
            "count": len(self.citations),
        }
