"""
Structured agent verdicts (MondayOS v2.4).

Replaces natural-language substring parsing of agent output with a single,
structured verdict object. Every agent run is reduced to exactly one
``AgentVerdict``, and the team workflow inspects only ``AgentVerdict.verdict`` —
never arbitrary prose. This eliminates false vetoes such as the word "blocker"
appearing in ordinary explanatory text.

Providers are asked (see agents.team.VERDICT_INSTRUCTION) to end their response
with a JSON object of the form::

    {"verdict": "pass" | "needs_changes" | "block",
     "confidence": "high" | "medium" | "low" | "...",
     "summary": "one line",
     "findings": ["..."],
     "recommendations": ["..."]}

Parsing rules, applied once, in order:

1. If the response contains such a JSON object (fenced or bare), it is used.
2. Otherwise, only an explicit *labelled* ``verdict:`` declaration is honoured
   (e.g. ``verdict: block``). Incidental prose — "this is a blocker", "a
   blocking issue" — is deliberately ignored: the ``verdict`` label is required,
   and a trailing word boundary stops "block" from matching inside "blocker".
3. Absent any explicit verdict, the outcome defaults to ``pass``. Blocking the
   pipeline requires a positive, structured signal; ambiguous prose never blocks.

The full, original response is preserved elsewhere (the run's
``execution.result_full``) for humans; this module only produces the structured
reduction the workflow consumes.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

PASS = "pass"
NEEDS_CHANGES = "needs_changes"
BLOCK = "block"
VALID_VERDICTS: tuple[str, ...] = (PASS, NEEDS_CHANGES, BLOCK)

# Synonyms mapped onto the canonical verdicts. Only ever consulted for the value
# of an explicit ``verdict`` field (JSON key or labelled declaration) — never for
# free-standing words in prose.
_BLOCK_WORDS = frozenset({
    "block", "blocked", "reject", "rejected", "veto", "vetoed",
    "fail", "failed", "not_approved", "do_not_merge", "deny", "denied",
})
_NEEDS_CHANGES_WORDS = frozenset({
    "needs_changes", "needs_change", "changes", "change_requested",
    "request_changes", "requested_changes", "revise", "rework", "changes_requested",
})
_PASS_WORDS = frozenset({
    "pass", "passed", "approve", "approved", "ok", "okay", "lgtm",
    "accept", "accepted", "go", "green",
})

# An explicit, labelled verdict declaration: `verdict: block`, `"verdict":"pass"`,
# `Verdict = needs_changes`. The capture is a whole token; normalisation decides
# its meaning. Requiring the `verdict` label (not a bare word) is what makes this
# robust against "blocker"/"blocking" in prose.
_VERDICT_DECL = re.compile(
    r'["\']?\bverdict\b["\']?\s*[:=]\s*["\']?([A-Za-z][A-Za-z_-]*)',
    re.IGNORECASE,
)


@dataclass
class AgentVerdict:
    """The structured reduction of one provider response."""

    verdict: str = PASS                 # pass | needs_changes | block
    confidence: str = ""                # free-form ("high"/"0.9"/…); string per schema
    summary: str = ""
    findings: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    source: str = "empty"               # json | parsed | empty — provenance, for humans

    @property
    def blocks(self) -> bool:
        """A hard stop — the pipeline must not advance past this stage."""
        return self.verdict == BLOCK

    @property
    def passed(self) -> bool:
        return self.verdict == PASS

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_verdict(value: Any) -> str:
    """Map a raw verdict token onto a canonical verdict. Unknown → ``pass``."""
    token = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if token in _BLOCK_WORDS:
        return BLOCK
    if token in _NEEDS_CHANGES_WORDS:
        return NEEDS_CHANGES
    if token in _PASS_WORDS:
        return PASS
    if token in VALID_VERDICTS:
        return token
    return PASS


def parse_verdict(text: str, role: str = "") -> AgentVerdict:
    """
    Reduce a provider response to a single structured ``AgentVerdict``.

    ``role`` is accepted for future role-aware defaults and to keep call sites
    self-documenting; the current logic does not branch on it.
    """
    raw = text or ""
    if not raw.strip():
        return AgentVerdict(source="empty")

    # 1. Prefer a real JSON object carrying a "verdict" key.
    obj = _find_verdict_json(raw)
    if obj is not None:
        return _from_json(obj)

    # 2. Fall back to an explicit, labelled verdict declaration only.
    summary = _first_line(raw)
    m = _VERDICT_DECL.search(raw)
    if m is not None:
        return AgentVerdict(
            verdict=normalize_verdict(m.group(1)),
            summary=summary,
            source="parsed",
        )

    # 3. No explicit verdict anywhere → pass. Prose never blocks.
    return AgentVerdict(verdict=PASS, summary=summary, source="parsed")


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _from_json(obj: dict[str, Any]) -> AgentVerdict:
    return AgentVerdict(
        verdict=normalize_verdict(obj.get("verdict")),
        confidence=_as_confidence(obj.get("confidence")),
        summary=str(obj.get("summary", "") or "").strip()[:2000],
        findings=_as_str_list(obj.get("findings")),
        recommendations=_as_str_list(obj.get("recommendations")),
        source="json",
    )


def _find_verdict_json(text: str) -> dict[str, Any] | None:
    """
    Return the first parseable JSON object that contains a ``verdict`` key.

    Looks in fenced ```json blocks first, then any balanced ``{...}`` object.
    Returns None if none is found — callers then fall back to labelled parsing.
    """
    for blob in _candidate_json_blobs(text):
        try:
            parsed = json.loads(blob)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict) and "verdict" in parsed:
            return parsed
    return None


_FENCED = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)


def _candidate_json_blobs(text: str):
    for m in _FENCED.finditer(text):
        yield m.group(1)
    yield from _balanced_objects(text)


def _balanced_objects(text: str):
    """Yield top-level ``{...}`` substrings by brace-depth scanning.

    Good enough for model output; each candidate is validated with json.loads by
    the caller, so a mis-sliced blob is simply skipped.
    """
    depth = 0
    start: int | None = None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    yield text[start : i + 1]
                    start = None


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        v = value.strip()
        return [v] if v else []
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            if isinstance(item, dict):
                # Common shapes: {"title": ...} / {"description": ...} / {"text": ...}
                s = item.get("title") or item.get("description") or item.get("text") or json.dumps(item, sort_keys=True)
            else:
                s = str(item)
            s = str(s).strip()
            if s:
                out.append(s)
        return out
    return [str(value).strip()]


def _as_confidence(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):  # avoid True→"True" surprises
        return str(value).lower()
    return str(value).strip()


def _first_line(text: str) -> str:
    for line in text.splitlines():
        s = line.strip().lstrip("#").strip()
        if s:
            return s[:240]
    return text.strip()[:240]
