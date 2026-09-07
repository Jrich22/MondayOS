"""
The question set, declared as data.

Cases are data rather than code so the set can be read in one place and so
scoring never branches on which project it is looking at. A case names the
*shape* of a question; the corpus supplies the noun.

`known_failing` is the field that keeps this honest. A benchmark whose cases all
pass is evidence the questions were chosen to pass, so today's real weaknesses —
`why-decision` retrieval, the strategic phrasings that route grounded — are
declared here as expected failures. They are measured and reported like any
other case, never skipped, and when S3 or S4 fixes one the run says so by name.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Dimension(Enum):
    ROUTING = "routing"
    RETRIEVAL = "retrieval"
    ISOLATION = "isolation"
    DISCOVERY = "discovery"
    CITATION = "citation"


@dataclass(frozen=True)
class Case:
    """
    One measurement.

    ``question`` may contain ``{symbol}`` or ``{topic}``, filled from the
    corpus's own nouns. ``known_failing`` records that MondayOS does not pass
    this today, with ``because`` stating what it does instead.
    """

    id: str
    dimension: Dimension
    question: str = ""
    expect_register: str = ""
    expect_grounded: bool | None = None
    expect_cites_kind: str = ""
    known_failing: bool = False
    because: str = ""

    def render(self, nouns: dict[str, str]) -> str:
        try:
            return self.question.format(**nouns)
        except KeyError:
            return self.question


# --------------------------------------------------------------------------- #
# Routing — a pure function of question text, so identical for every corpus.
# --------------------------------------------------------------------------- #

_LOOKUPS = [
    ("where-implemented", "Where is {symbol} implemented?"),
    ("show-references", "Show me every reference to {symbol}."),
    ("find-usages", "Find every place {symbol} is used."),
    ("which-file", "What file owns {topic}?"),
    ("what-changed", "What changed in the last three commits?"),
    ("how-does-work", "How does {topic} work?"),
    ("explain", "Explain {topic}."),
    ("what-does-do", "What does {symbol} do?"),
]

_STRATEGIC = [
    ("build-next", "What should we build next?", False, ""),
    ("neglecting", "What are we neglecting?", False, ""),
    ("biggest-risk", "What is our biggest technical risk?", False, ""),
    ("demo-ready", "Are we ready for a demo?", False, ""),
    ("investor", "What would an investor ask?", False, ""),
    # The five strategic phrasings that route grounded today. Declared, measured,
    # and left alone: S2 records the weakness, S4 fixes it.
    (
        "blocking-us",
        "What is blocking us?",
        True,
        "routes grounded; no pattern matches this phrasing",
    ),
    (
        "how-healthy",
        "How healthy is this codebase?",
        True,
        "routes grounded; no pattern matches this phrasing",
    ),
    (
        "refactor-or-ship",
        "Should we refactor or ship?",
        True,
        "routes grounded; no pattern matches this phrasing",
    ),
    (
        "highest-leverage",
        "What is the highest-leverage thing to do?",
        True,
        "routes grounded; 'leverage' pattern requires a different shape",
    ),
    (
        "say-more",
        "Say more about that.",
        True,
        "routes grounded; no back-reference pattern covers it",
    ),
]

ROUTING_CASES: tuple[Case, ...] = tuple(
    [
        Case(
            id=f"routing.lookup.{name}",
            dimension=Dimension.ROUTING,
            question=question,
            expect_register="grounded",
        )
        for name, question in _LOOKUPS
    ]
    + [
        Case(
            id=f"routing.strategic.{name}",
            dimension=Dimension.ROUTING,
            question=question,
            expect_register="executive",
            known_failing=failing,
            because=because,
        )
        for name, question, failing, because in _STRATEGIC
    ]
)

# --------------------------------------------------------------------------- #
# Retrieval — does a question reach grounded evidence at all.
# --------------------------------------------------------------------------- #

RETRIEVAL_CASES: tuple[Case, ...] = (
    Case(
        id="retrieval.where-implemented",
        dimension=Dimension.RETRIEVAL,
        question="Where is {symbol} implemented?",
        expect_grounded=True,
    ),
    Case(
        id="retrieval.what-is-this",
        dimension=Dimension.RETRIEVAL,
        question="What is this project?",
        expect_grounded=True,
    ),
    Case(
        id="retrieval.what-changed",
        dimension=Dimension.RETRIEVAL,
        question="What changed recently?",
        expect_grounded=True,
    ),
    Case(
        id="retrieval.where-documented",
        dimension=Dimension.RETRIEVAL,
        question="Where is {topic} documented?",
        expect_grounded=True,
    ),
    # why-decision retrieves on ADR *titles* only, so a question whose terms
    # appear in an ADR body reaches nothing. Recorded, not fixed, in S2.
    Case(
        id="retrieval.why-decision",
        dimension=Dimension.RETRIEVAL,
        question="Why was {topic} designed this way?",
        expect_grounded=True,
        expect_cites_kind="decision",
        known_failing=True,
        because="decision retrieval matches ADR titles only, not bodies",
    ),
)

ALL_CASES: tuple[Case, ...] = ROUTING_CASES + RETRIEVAL_CASES


def by_dimension(dimension: Dimension) -> tuple[Case, ...]:
    return tuple(c for c in ALL_CASES if c.dimension is dimension)
