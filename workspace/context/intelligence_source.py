"""
Project intelligence as a context source.

The Context Engine's other adapters answer "what exists": tasks, ADR titles,
filenames, commits. This one answers "what bears on *this question*" — the
symbol definitions, decision records, tests and documentation the question
engine retrieved, with the file and line to open.

It is a context source rather than a separate path into the model for a specific
reason: everything the Context Engine assembles is budgeted, attributed and
redacted in one place (ADR-016, ADR-017). A second route into the prompt would
be a second place those rules have to hold, and eventually one of them would be
wrong.

The engine runs *before* the model, so the evidence is fixed before any
generation happens. What the model can say is bounded by what the project
actually contains, and the same citations are shown to the operator — the panel
and the prompt cannot disagree.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from intelligence.evidence import CitationKind
from intelligence.questions import Answer
from workspace.context import relevance
from workspace.context.adapters import safe_source
from workspace.context.snapshot import ContextSource

# How many citations to render. A prompt does not need forty; a reader following
# the evidence does not read forty either.
MAX_CITATIONS = 14

# Which citation kinds map to which retrieval reason, so the "why was this here"
# vocabulary stays the same one the rest of the snapshot uses.
_REASON: dict[CitationKind, str] = {
    CitationKind.SYMBOL: "definition",
    CitationKind.DECISION: relevance.REASON_ARCHITECTURE,
    CitationKind.TASK: relevance.REASON_ACTIVE_TASK,
    CitationKind.COMMIT: relevance.REASON_RECENT,
    CitationKind.TEST: "covering-test",
    CitationKind.FILE: relevance.REASON_KEYWORD,
    CitationKind.PULL_REQUEST: relevance.REASON_RECENT,
    CitationKind.KNOWLEDGE: relevance.REASON_KEYWORD,
}


def intelligence_source(
    project: str,
    ask: Callable[[], Answer | None],
    query: str = "",
) -> ContextSource:
    """
    Evidence the question engine retrieved for this request.

    Takes a callable so the (relatively expensive) retrieval happens inside the
    fail-closed wrapper: an indexer that breaks makes this source empty and
    leaves the rest of the snapshot intact, rather than taking the conversation
    down with it.
    """

    def build() -> list[str]:
        answer = ask()
        if answer is None or not answer.grounded:
            return []

        items: list[str] = [f"Question understood as: {answer.intent.value}"]
        if answer.subject:
            items.append(f"Subject: {answer.subject}")

        for line in answer.finding.splitlines():
            if line.strip():
                items.append(line.rstrip())

        items.append(answer.evidence.summary())
        for citation in answer.evidence.citations[:MAX_CITATIONS]:
            # Every reference is navigable: path plus line range where one
            # exists, artefact id otherwise.
            items.append(f"  {citation.kind.value}: {citation.display()} — {citation.because}")
        return items

    # No query is passed to safe_source deliberately. The evidence order *is* the
    # ranking: the question engine already ordered it by how each source was
    # retrieved, with the finding first and citations after. Ranking it again
    # against the raw query would interleave the summary lines with the citations
    # and lose the engine's reasoning — and an empty query is exactly how the
    # ranker is told to preserve arrival order.
    source = safe_source(
        "intelligence",
        "Project intelligence",
        "deterministic project index",
        build,
    )
    if source.items:
        source.reasons = ["retrieved-evidence"] * len(source.items)
    return source


def citations_for_ui(answer: Answer | None) -> list[dict[str, Any]]:
    """The same citations, in the shape the workspace API returns them."""
    if answer is None:
        return []
    return [c.to_dict() for c in answer.evidence.citations]
