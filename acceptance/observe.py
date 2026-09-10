"""
Measuring one live turn.

The deterministic benchmark inspects `Evidence` objects the retrieval layer
built. This one reads **what the model actually said**, because that is what a
person acts on: a path in the answer is a path they will open, and a decision the
answer names is one they will go looking for. An answer citing a file that does
not exist is wrong even when the retrieval underneath it was perfect, and only
reading the output catches that.

Everything here is mechanical. Nothing scores whether an answer was *good* --
that judgement belongs to a human reading the review package, and a number
invented to stand in for it would be the least trustworthy figure in the report
and the one people would quote.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.boundary import outside

# A path in prose. Anchored on a real source extension so ordinary sentences do
# not become citations: "e.g. the router" is not a file, `reasoning/router.py:110`
# is. The optional `:line` is what makes a citation navigable.
_PATH = re.compile(
    r"\b((?:[\w.\-]+/)*[\w.\-]+"
    r"\.(?:py|ts|tsx|js|jsx|md|json|toml|yaml|yml|sh|css|html))"
    r"(?::(\d+))?",
)

# `ADR-017`, however the answer punctuates it. The width matches
# `intelligence/index.py`, so the harness and the index agree on what an ADR
# reference is; a looser pattern here made this over-eager (RC1/ACC-008).
_ADR = re.compile(r"\bADR[-_ ]?(\d{3,4})\b", re.I)

# An abbreviated git SHA as it appears in a commit line. Seven or more hex
# characters, which is what `git log --format=%h` produces and what an answer
# quoting history contains. Bounded below so ordinary words such as "deadbeef"
# in prose are not mistaken for history -- and checked against the project's own
# log before anything is concluded, so a false positive costs nothing.
_COMMIT = re.compile(r"\b([0-9a-f]{7,40})\b")

# A number quoted next to one of the three scores. Deliberately narrow: it looks
# for a percentage or decimal within a short span of the score's own name, so a
# stray number elsewhere in the prose is not read as a claim about confidence.
# A phrase the answer presents *as* an initiative, either introduced by the word
# or followed by it. Anything looser turns ordinary nouns into accusations.
_CLAIMED_INITIATIVE = re.compile(
    r"(?:initiative|capability)\s*[:\-]?\s*[\"'`]?"
    r"([A-Za-z][\w \-]{2,40}?)[\"'`]?\s*(?:initiative|capability|[.,;)\n])"
    r"|[\"'`]([A-Za-z][\w \-]{2,40}?)[\"'`]\s+(?:initiative|capability)",
    re.I,
)

_QUOTED = {
    "confidence": re.compile(
        r"confiden(?:ce|t)\D{0,24}?(\d{1,3}(?:\.\d+)?)\s*%|"
        r"confiden(?:ce|t)\D{0,24}?(0\.\d+)",
        re.I,
    ),
    "evidence_strength": re.compile(
        r"evidence(?:\s+strength)?\D{0,24}?(\d{1,3}(?:\.\d+)?)\s*%|"
        r"evidence(?:\s+strength)?\D{0,24}?(0\.\d+)",
        re.I,
    ),
    "execution_risk": re.compile(
        r"(?:execution\s+)?risk\D{0,24}?(\d{1,3}(?:\.\d+)?)\s*%|"
        r"(?:execution\s+)?risk\D{0,24}?(0\.\d+)",
        re.I,
    ),
}


@dataclass
class CitationCheck:
    """Where the answer pointed, and whether a reader could follow it."""

    total: int = 0
    resolved: int = 0
    unresolved: list[str] = field(default_factory=list)
    outside_boundary: list[str] = field(default_factory=list)
    with_line: int = 0
    invalid_lines: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "resolved": self.resolved,
            "with_line": self.with_line,
            "unresolved": self.unresolved[:10],
            "outside_boundary": self.outside_boundary[:10],
            "invalid_lines": self.invalid_lines[:10],
        }


def check_citations(answer: str, root: Path, boundaries: frozenset[str]) -> CitationCheck:
    """
    Every path the answer names, checked against the project on disk.

    Three distinct failures, kept apart because they mean different things: a
    path that does not exist is an invented citation, a path inside a nested
    project is a boundary breach, and a line past the end of a real file is a
    fabricated location in a genuine file.
    """
    check = CitationCheck()
    seen: set[str] = set()
    for match in _PATH.finditer(answer or ""):
        raw, line_text = match.group(1), match.group(2)
        key = f"{raw}:{line_text or ''}"
        if key in seen:
            continue
        seen.add(key)
        check.total += 1

        if outside(raw, boundaries):
            check.outside_boundary.append(raw)
            continue
        target = root / raw
        if not target.is_file():
            # Not every path-shaped token is a citation -- a model may name a file
            # it is proposing to create. Recorded as unresolved rather than as a
            # boundary breach, and gate 11 only judges the ones that resolve.
            check.unresolved.append(raw)
            continue
        check.resolved += 1
        if line_text:
            check.with_line += 1
            try:
                length = len(target.read_text(encoding="utf-8", errors="replace").splitlines())
            except OSError:
                check.invalid_lines.append(key)
                continue
            number = int(line_text)
            if number < 1 or number > length:
                check.invalid_lines.append(f"{raw}:{number} (file has {length} lines)")
    return check


def cited_decisions(answer: str) -> list[str]:
    """ADR identifiers the answer names, normalised."""
    return sorted({f"ADR-{int(m.group(1)):03d}" for m in _ADR.finditer(answer or "")})


def cited_commits(answer: str) -> list[str]:
    """
    Commit-shaped tokens the answer names.

    Candidates only. Whether one belongs to this project is decided against the
    project's own git log, never guessed from the text.
    """
    return sorted({m.group(1).lower() for m in _COMMIT.finditer(answer or "")})


def quoted_scores(answer: str) -> dict[str, float]:
    """
    Score values the answer states, as fractions.

    Only what is stated near the score's own name. An answer that quotes nothing
    yields nothing, which is not a failure: describing a recommendation without
    reciting its numbers is perfectly good writing. Gate 6 judges a quoted number
    that disagrees, never a number that was never given.
    """
    found: dict[str, float] = {}
    for name, pattern in _QUOTED.items():
        match = pattern.search(answer or "")
        if not match:
            continue
        percent, fraction = match.group(1), match.group(2)
        if percent is not None:
            found[name] = round(float(percent) / 100.0, 4)
        elif fraction is not None:
            found[name] = round(float(fraction), 4)
    return found


# Words that make a phrase a sentence rather than a name. A capability is called
# "Billing" or "AI Workspace"; it is never called "and reduce the risk of future
# changes". Any of these disqualifies a candidate outright.
_NOT_A_NAME = frozenset(
    {
        "and",
        "or",
        "but",
        "the",
        "a",
        "an",
        "of",
        "to",
        "for",
        "with",
        "from",
        "that",
        "this",
        "these",
        "those",
        "it",
        "its",
        "we",
        "our",
        "us",
        "you",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "will",
        "would",
        "should",
        "could",
        "can",
        "may",
        "might",
        "must",
        "do",
        "does",
        "did",
        "have",
        "has",
        "had",
        "in",
        "on",
        "at",
        "by",
        "as",
        "if",
        "than",
        "then",
        "because",
        "which",
        "when",
        "while",
        "not",
        "no",
        "more",
        "most",
    }
)

# A capability name is short. Three words covers "AI Workspace", "Growth BOT",
# "Knowledge System"; beyond that a phrase is prose.
_MAX_NAME_WORDS = 3


def _is_name_shaped(candidate: str) -> bool:
    """
    Whether a phrase is an entity name rather than a piece of a sentence.

    Absence of stopwords is not enough. "took priority", "directly" and
    "s maturity" carry none and are still fragments, and gate 2 reported all
    three as invented capabilities. A name has to look like a name: every word
    capitalised or an acronym, the way `Billing`, `AI Workspace`, `Growth BOT`
    and `Knowledge System` are — which is also how discovery names them.

    Deliberately strict, and the strictness runs one way. A missed invention is a
    gap in the report; a false one is an accusation, and this has made three.
    """
    words = [w for w in re.split(r"[\s\-_]+", candidate.strip()) if w]
    if not words or len(words) > _MAX_NAME_WORDS:
        return False
    if any(w.lower() in _NOT_A_NAME for w in words):
        return False
    for word in words:
        if not re.fullmatch(r"[A-Za-z][\w.]*", word):
            return False
        # Capitalised, or an all-caps acronym. A lowercase word in the middle of
        # prose is prose.
        if not (word[0].isupper() or word.isupper()):
            return False
    return True


def named_initiatives(answer: str, discovered: list[str]) -> dict[str, list[str]]:
    """
    Which discovered capabilities the answer names, and which it invented.

    Discovery's own output is the canonical namespace: a phrase matching a known
    initiative is a reference, and anything else has to clear `_is_name_shaped`
    before it can be called an invention. Both filters push the same way --
    towards missing a real invention rather than manufacturing a false one.
    """
    known = {d.lower() for d in discovered}
    claimed: set[str] = set()
    for match in _CLAIMED_INITIATIVE.finditer(answer or ""):
        name = (match.group(1) or match.group(2) or "").strip()
        if not name:
            continue
        if name.lower() in known or _is_name_shaped(name):
            claimed.add(name)
    invented = sorted(n for n in claimed if n.lower() not in known)
    return {"named": sorted(claimed), "invented": invented}
