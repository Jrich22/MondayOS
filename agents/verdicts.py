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
   An unrecognised ``verdict`` value inside it is ``invalid`` — not a pass.
2. Otherwise, an explicit *labelled* ``verdict:`` declaration may still produce
   a CONSERVATIVE outcome (``block`` / ``needs_changes``), because a stop signal
   is safe to honour from any source. A labelled ``verdict: pass`` is NOT
   honoured — prose can never grant a pass.
3. Absent a structured verdict, the outcome is ``invalid``. Incidental prose —
   "this is a blocker", "looks good", "Checkpoint 7: PASS" — is ignored in both
   directions.

**Only a well-formed JSON verdict object can produce ``pass``.** This is the
central invariant: a missing, malformed, or truncated response is ``invalid``
and the team workflow refuses to advance past a blocking role that produced one.

This replaced an earlier design in which an absent verdict defaulted to ``pass``
on the theory that "ambiguous prose never blocks". That reasoning was backwards
for a quality gate: it meant a provider that returned nothing, errored into an
empty body, or was cut off by the output-token limit before emitting its JSON
was recorded as having approved the work. Several QA / Security / Reviewer runs
passed that way without ever producing a verdict.

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

# No usable structured verdict was produced: missing, malformed, truncated, or
# carrying an unrecognised value. Never an approval.
INVALID = "invalid"

# The three outcomes a provider may legitimately declare.
VALID_VERDICTS: tuple[str, ...] = (PASS, NEEDS_CHANGES, BLOCK)
# Everything a stage verdict may be, including the failure state.
ALL_VERDICTS: tuple[str, ...] = (PASS, NEEDS_CHANGES, BLOCK, INVALID)

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
    """
    The structured reduction of one provider response.

    Defaults to ``invalid``: constructing a verdict without an explicit,
    recognised value must never read as an approval.
    """

    verdict: str = INVALID              # pass | needs_changes | block | invalid
    confidence: str = ""                # free-form ("high"/"0.9"/…); string per schema
    summary: str = ""
    findings: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    source: str = "empty"               # json | parsed | empty — provenance, for humans
    reason: str = ""                    # why a verdict is invalid, for humans/audit

    @property
    def blocks(self) -> bool:
        """A hard stop — the pipeline must not advance past this stage."""
        return self.verdict == BLOCK

    @property
    def passed(self) -> bool:
        return self.verdict == PASS

    @property
    def is_valid(self) -> bool:
        """True when the provider declared a recognised verdict."""
        return self.verdict in VALID_VERDICTS

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_verdict(value: Any) -> str:
    """
    Map a raw verdict token onto a canonical verdict.

    An unrecognised or empty token is ``invalid`` — never ``pass``. A quality
    gate that cannot read the answer has not been told the work is acceptable.
    """
    token = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if token in _BLOCK_WORDS:
        return BLOCK
    if token in _NEEDS_CHANGES_WORDS:
        return NEEDS_CHANGES
    if token in _PASS_WORDS:
        return PASS
    if token in VALID_VERDICTS:
        return token
    return INVALID


def parse_verdict(text: str, role: str = "", truncated: bool = False) -> AgentVerdict:
    """
    Reduce a provider response to a single structured ``AgentVerdict``.

    Args:
        text:      The full provider response.
        role:      Accepted to keep call sites self-documenting; not branched on.
        truncated: True when the provider stopped because it hit the output-token
                   limit. A truncated response that still carries a complete JSON
                   verdict is honoured — the verdict is requested first precisely
                   so it survives truncation — but one without is ``invalid``,
                   never a silent pass.

    Only a well-formed JSON verdict object can yield ``pass``.
    """
    raw = text or ""
    if not raw.strip():
        return AgentVerdict(source="empty", reason="provider returned an empty response")

    summary = _first_line(raw)

    # 1. Prefer a real JSON object carrying a "verdict" key.
    obj = _find_verdict_json(raw)
    if obj is not None:
        verdict = _from_json(obj)
        if verdict.verdict == INVALID:
            verdict.reason = (
                f"structured verdict carried an unrecognised value {obj.get('verdict')!r}"
            )
        return verdict

    # Truncation is the *cause* whenever it applies, so it outranks the more
    # specific descriptions below — a cut-off response explains itself.
    truncation_reason = "response was truncated before a complete structured verdict was emitted"

    # 2. No parseable JSON. A labelled declaration may still produce a
    #    CONSERVATIVE outcome — a stop signal is safe from any source — but
    #    never a pass. Note a truncated JSON object lands here too: its opening
    #    `"verdict": "pass"` text matches the label pattern, and it must not be
    #    honoured on the strength of a fragment.
    m = _VERDICT_DECL.search(raw)
    if m is not None:
        declared = normalize_verdict(m.group(1))
        if declared in (BLOCK, NEEDS_CHANGES):
            return AgentVerdict(verdict=declared, summary=summary, source="parsed")
        return AgentVerdict(
            summary=summary,
            source="parsed",
            reason=truncation_reason if truncated else (
                f"labelled verdict {m.group(1)!r} found without a structured JSON "
                "object; prose cannot grant a pass"
            ),
        )

    # 3. No structured verdict at all.
    return AgentVerdict(
        summary=summary,
        source="parsed",
        reason=truncation_reason if truncated else "response contained no structured JSON verdict",
    )


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
