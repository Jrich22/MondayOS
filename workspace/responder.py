"""
The responder seam — where model routing will attach.

The workspace never depends on a provider. It depends on ``WorkspaceResponder``,
a protocol with one method. Increment 1 ships ``ProviderWorkspaceResponder``,
which wraps a single MondayOS ``AIProvider``; increment 4 will add a
``RoutingWorkspaceResponder`` that picks per request. Neither the Conversation
model, the Context Engine, nor the service changes when that happens (ADR-018).

Two details make the seam real rather than decorative.

**The request carries structure, not a rendered prompt.** A responder receives
the project, the snapshot, and the conversation history as separate fields. Hand
a router a finished prompt string and it has nothing left to route on — "which
model suits this request" becomes unanswerable once context is flattened.

**No vendor SDK is imported here or anywhere else in this package.** The provider
arrives injected. Which model runs is MondayOS configuration, and this module
works unchanged when that changes.
"""

from __future__ import annotations

import html
import re
import unicodedata
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from brain.providers.base import AIProvider, ProviderError
from reasoning.models import Assessment, Mode
from workspace.authority import EvidenceAuthority, NullAuthority, Resolution
from workspace.context.snapshot import ContextSnapshot
from workspace.models import Message, MessageRole

# Output budget for one conversational turn. An initial default, configurable per
# responder — not a product rule. Long enough for a substantive answer about a
# project, short enough that a runaway generation is bounded.
DEFAULT_MAX_TOKENS = 2000

# An executive answer carries eight sections, three scores and the alternatives
# that lost. That does not fit in a conversational budget: the first live run of
# the format stopped mid-sentence inside the final heading. Raised deliberately
# rather than by trimming the format, because the sections a truncated answer
# drops are the last ones — evidence strength, confidence and risk — which are
# exactly the ones that make the advice auditable.
#
# Set from measurement, not taste: a benchmark run against a real repository with
# accumulated conversation history reached ~4000 tokens on the longest strategic
# answer, so 4000 was the number that truncated. Both budgets remain hard ceilings
# — an answer that exceeds them is still marked incomplete rather than passed off
# as finished.
EXECUTIVE_MAX_TOKENS = 6000

# How many prior turns to replay. Recent dialogue is high-value context and cheap;
# the whole history is neither. Increment 2 replaces this with summarised history.
DEFAULT_HISTORY_TURNS = 12

# The standing instruction, in two registers.
#
# The original single instruction told the model to say plainly when the context
# did not contain an answer. That is why hallucinations were low — and it is also
# why Monday read as a search engine: asked what to build next, it correctly
# reported which documents it lacked.
#
# The fix is not to loosen grounding, which would trade the property that made it
# trustworthy. It is to let inference happen *labelled*. A conclusion presented as
# a conclusion, with its confidence attached, is safe in a way an unmarked one is
# not, and MondayOS now computes both before the model is called (see `reasoning`).
SYSTEM_INSTRUCTION = (
    "Confidence, evidence strength and execution risk are computed by MondayOS and "
    "supplied to you. Report them, round them, or describe them in words -- but never "
    "invent, override or supplement them. If you want to express a judgement MondayOS "
    "did not compute, say it in prose without attaching a number to it. "
    "When citing repository evidence -- a commit, file, decision, task or pull "
    "request -- cite the supplied evidence handle in square brackets, like [E1], "
    "rather than writing the identifier yourself. MondayOS resolves handles to real "
    "identifiers after generation, and a handle that was never supplied is caught as "
    "a fabricated reference. Cite only identifiers MondayOS supplied above. "
    "Never construct, guess or "
    "complete a commit SHA, ADR or DEC id, task id, pull request number, file path "
    "or symbol name. If the evidence does not contain what you need, say what is "
    "missing — an invented reference is worse than an acknowledged gap, because a "
    "reader cannot tell them apart.\n\n"
    "You are MondayOS, an AI operating system, answering about one specific project. "
    "You have been given a context snapshot assembled from that project's registry entry, "
    "documentation, architecture decisions, tasks, knowledge base and git state, and may "
    "also have been given a reasoning assessment computed from the same project.\n\n"
    "Ground your answer in that material. Keep the distinction between what the project "
    "states, what follows from it, and what you are recommending — but do not stop "
    "because the record is incomplete. Where the snapshot has no direct answer, reason "
    "from what is present and mark that reasoning as inference. Never present an "
    "inference as a documented fact about this codebase.\n\n"
    "Do not open by listing what the context lacks. Lead with the most useful thing you "
    "can say, and name a missing document only where it changes what the reader should "
    "do next.\n\n"
    "The context is scoped to this project alone. Do not speculate about other projects."
)

# The executive register. Used when MondayOS has routed the question as strategic
# and has already done the reasoning.
#
# The critical line is the one forbidding the model to invent confidence numbers.
# Every score in the assessment was computed from evidence by `reasoning.confidence`;
# a model that helpfully adds its own would produce something that looks identical
# on screen and means nothing, which would quietly destroy the one signal telling
# the reader how much to trust the rest.
EXECUTIVE_INSTRUCTION = (
    "Confidence, evidence strength and execution risk are computed by MondayOS and "
    "supplied to you. Report them, round them, or describe them in words -- but never "
    "invent, override or supplement them. If you want to express a judgement MondayOS "
    "did not compute, say it in prose without attaching a number to it. "
    "When citing repository evidence -- a commit, file, decision, task or pull "
    "request -- cite the supplied evidence handle in square brackets, like [E1], "
    "rather than writing the identifier yourself. MondayOS resolves handles to real "
    "identifiers after generation, and a handle that was never supplied is caught as "
    "a fabricated reference. Cite only identifiers MondayOS supplied above. "
    "Never construct, guess or "
    "complete a commit SHA, ADR or DEC id, task id, pull request number, file path "
    "or symbol name. If the evidence does not contain what you need, say what is "
    "missing — an invented reference is worse than an acknowledged gap, because a "
    "reader cannot tell them apart.\n\n"
    "You are MondayOS, acting as the technical and product lead for one specific "
    "project.\n\n"
    "You have been given a reasoning assessment built deterministically from this "
    "project's capabilities, index, relationship graph, decision log, task store and "
    "git history. Its facts, inferences, capability health, recommendations, "
    "alternatives, confidence scores and execution risks were computed by MondayOS, "
    "not by you. Narrate and explain that assessment: do not recompute it, do not "
    "invent additional confidence or risk numbers, and do not contradict a stated "
    "score.\n\n"
    "When the assessment reports record/reality drift, say so plainly and early. It "
    "means the project record disagrees with the code, so figures derived from the "
    "record are unreliable. MondayOS has not changed any task and you must not imply "
    "it has; report the disagreement and leave the correction to the reader.\n\n"
    "Lead with capabilities, not files. The reader thinks in products — AI Workspace, "
    "Billing, RSVP — and drills into implementation only when it changes a decision. "
    "A capability the assessment marks as needing attention belongs near the top.\n\n"
    "Answer as an experienced CTO would. Take a position, say which thing to do "
    "first, and be specific about what it costs and what you are giving up.\n\n"
    "Structure the answer under these headings, in this order:\n"
    "Facts — what this repository directly supports.\n"
    "Inferences — what follows from those facts, marked as reasoning not record.\n"
    "Alternatives Considered — the options that were weighed and why each lost.\n"
    "Tradeoffs — what the recommended path costs.\n"
    "Recommendation — what you advise, ranked.\n"
    "Evidence Strength — how well-supported the underlying facts are.\n"
    "Recommendation Confidence — how sure the call itself is.\n"
    "Execution Risk — how likely carrying it out is to go wrong.\n\n"
    "The last three are different measures and often disagree. Strong evidence for a "
    "risky change is a normal and important situation; if they diverge, say so "
    "plainly rather than averaging them into one impression.\n\n"
    "Incomplete documentation is normal and is never a reason to decline to advise. "
    "Reason from what exists. The context is scoped to this project alone."
)


# The continuation register. Same budget and same structure as a fresh executive
# turn — what changes is that the decision already exists, so the model is
# explaining one rather than making one.
#
# The forbidding clauses carry the weight. A model asked about a recommendation
# it can see will happily re-derive a better one, and then the user is reading
# advice about a decision they never received.
CONTINUATION_INSTRUCTION = (
    "Confidence, evidence strength and execution risk are computed by MondayOS and "
    "supplied to you. Report them, round them, or describe them in words -- but never "
    "invent, override or supplement them. If you want to express a judgement MondayOS "
    "did not compute, say it in prose without attaching a number to it. "
    "When citing repository evidence -- a commit, file, decision, task or pull "
    "request -- cite the supplied evidence handle in square brackets, like [E1], "
    "rather than writing the identifier yourself. MondayOS resolves handles to real "
    "identifiers after generation, and a handle that was never supplied is caught as "
    "a fabricated reference. Cite only identifiers MondayOS supplied above. "
    "Never construct, guess or "
    "complete a commit SHA, ADR or DEC id, task id, pull request number, file path "
    "or symbol name. If the evidence does not contain what you need, say what is "
    "missing — an invented reference is worse than an acknowledged gap, because a "
    "reader cannot tell them apart.\n\n"
    "You are MondayOS, acting as the technical and product lead for one specific "
    "project. The user is asking a follow-up about a recommendation you already "
    "gave them in this conversation.\n\n"
    "The prior recommendation, its alternatives, its evidence and its three scores "
    "are given below exactly as they were shown to the user. They were computed by "
    "MondayOS, not by you.\n\n"
    "Answer about THAT recommendation. Do not re-rank the options, do not choose a "
    "different winner, and do not recompute the analysis. If you believe a "
    "different option is better, say so as a caveat — never by quietly switching "
    "which one you are discussing.\n\n"
    "Do not ask which recommendation is meant. It is stated below; asking would be "
    "a failure to use what you were given.\n\n"
    "Do not invent confidence or risk numbers, and do not contradict a stated "
    "score.\n\n"
    "Read the STATUS line and honour it. When the prior decision is marked stale, "
    "say plainly that the project has changed since the recommendation was made "
    "and name what changed; when it is marked obsolete, report the recommendation "
    "as superseded rather than as advice to act on. Never present a stale "
    "recommendation as current.\n\n"
    "Separate what was decided from what is true now. 'Here is what I recommended "
    "and why' and 'here is what the evidence says today' are different claims, and "
    "collapsing them hides the only thing that would change the answer.\n\n"
    "Reason and explain only. Do not create tasks, files or artifacts, and do not "
    "claim to have done so.\n\n"
    "The context is scoped to this project alone."
)


class EvidenceVerdict(Enum):
    """
    What validation concluded about one identifier-shaped claim.

    ``UNVERIFIABLE`` is the member the red-team review forced into existence.
    Validation used to have two outcomes, so a class MondayOS could not check at
    all -- symbols with no index, commits on a project whose git source had been
    dropped -- came back indistinguishable from a clean one. "We did not look"
    and "we looked and it was fine" are different facts, and only the second is a
    guarantee.
    """

    VERIFIED = "verified"
    UNSUPPORTED = "unsupported"
    UNVERIFIABLE = "unverifiable"
    NON_EVIDENCE = "non_evidence"


# The identifier classes MondayOS protects. Order is display order.
CLASSES = ("commit", "decision", "task", "pull_request", "file", "symbol")


@dataclass(frozen=True)
class EvidenceSet:
    """
    Everything retrieval put in front of the model for this turn.

    One of two authorities, and the weaker one. Membership here means "the model
    was literally given this", which is sufficient to verify a claim but never
    necessary: the project's own records (``EvidenceAuthority``) remain able to
    confirm an identifier the context budget trimmed away.

    Matching is **exact**. It used to resolve abbreviations in both directions,
    which meant any string beginning with a real seven-character prefix matched
    it -- `5c44663deadbeef...` verified as genuine. Abbreviation resolution is
    git's job and lives in the authority.
    """

    commits: frozenset[str] = frozenset()
    decisions: frozenset[str] = frozenset()
    tasks: frozenset[str] = frozenset()
    pull_requests: frozenset[str] = frozenset()
    paths: frozenset[str] = frozenset()
    symbols: frozenset[str] = frozenset()

    @property
    def empty(self) -> bool:
        return not (
            self.commits
            or self.decisions
            or self.tasks
            or self.pull_requests
            or self.paths
            or self.symbols
        )

    def holds(self, kind: str, identifier: str) -> bool:
        """Whether retrieval supplied this exact identifier."""
        lowered = identifier.lower()
        if kind == "commit":
            return lowered in self.commits
        if kind == "decision":
            return _normalise_id(identifier) in {_normalise_id(d) for d in self.decisions}
        if kind == "task":
            return _normalise_id(identifier) in {_normalise_id(t) for t in self.tasks}
        if kind == "pull_request":
            return identifier.lstrip("#") in self.pull_requests
        if kind == "file":
            cleaned = identifier.lstrip("./")
            return any(p.lstrip("./") == cleaned for p in self.paths)
        if kind == "symbol":
            return identifier in self.symbols
        return False


# --------------------------------------------------------------------------- #
# structured citation protocol
# --------------------------------------------------------------------------- #

# A handle the model is asked to cite instead of writing an identifier itself.
# Opaque on purpose: there is nothing in `[E3]` for a model to construct a
# plausible-looking variant of, which is the failure every heuristic below exists
# to catch after the fact.
# A citation group: one handle, or several in one bracket. Models write
# `[E2, E3]` naturally, and the single-handle form matched nothing at all there --
# so the group was neither resolved into real identifiers nor reported as unknown.
# The reader saw `[E2, E3]`: prompt-internal labels, attached to a factual claim,
# with the structured path silently bypassed.
HANDLE = re.compile(r"\[\s*(E\d{1,3}(?:\s*,\s*E\d{1,3})*)\s*\]", re.I)
_HANDLE_MEMBER = re.compile(r"E\d{1,3}", re.I)


@dataclass(frozen=True)
class EvidenceHandle:
    """One retrieved fact, addressable by a short opaque label."""

    label: str
    kind: str
    reference: str
    description: str = ""

    def render(self) -> str:
        tail = f" - {self.description}" if self.description else ""
        return f"[{self.label}] {self.kind} {self.reference}{tail}"


@dataclass(frozen=True)
class EvidenceFinding:
    """One identifier-shaped claim an answer made, and how it resolved."""

    kind: str
    identifier: str
    verdict: EvidenceVerdict
    via: str = "heuristic"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "identifier": self.identifier,
            "verdict": self.verdict.value,
            "via": self.via,
        }


@dataclass(frozen=True)
class ClassReport:
    """What validation actually managed to do for one identifier class."""

    claims_found: int = 0
    verified: int = 0
    unsupported: int = 0
    unverifiable: int = 0
    authority_available: bool = False

    @property
    def validated(self) -> bool:
        """
        Whether anything was meaningfully checked.

        A class with claims it could not resolve has not been validated, however
        many times it was consulted. This is the flag that stops a symbols-only
        answer reporting success having checked nothing.
        """
        return self.claims_found > 0 and self.unverifiable == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "claims_found": self.claims_found,
            "verified": self.verified,
            "unsupported": self.unsupported,
            "unverifiable": self.unverifiable,
            "authority_available": self.authority_available,
        }


@dataclass(frozen=True)
class EvidenceReport:
    """Everything validation concluded about one answer."""

    findings: list[EvidenceFinding] = field(default_factory=list)
    classes: dict[str, ClassReport] = field(default_factory=dict)
    unknown_handles: list[str] = field(default_factory=list)
    resolved_handles: int = 0

    @property
    def checked(self) -> bool:
        """
        True only when at least one class was genuinely validated.

        Deliberately not "validation ran". The previous metadata reported
        ``checked: true`` whenever an evidence set existed, including for classes
        that were skipped entirely, which is how a turn could claim a clean bill
        of health without a single identifier being examined.
        """
        return any(report.validated for report in self.classes.values())

    @property
    def blocking(self) -> list[EvidenceFinding]:
        """
        Claims that may not be shown as verified fact.

        Unsupported and unverifiable both block. An identifier the project's
        records reject and one MondayOS could not check are different diagnoses
        but the same guarantee: neither has been proven, so neither is presented
        as proof.
        """
        return [
            f
            for f in self.findings
            if f.verdict in (EvidenceVerdict.UNSUPPORTED, EvidenceVerdict.UNVERIFIABLE)
        ]

    def to_metadata(self, *, attempted: bool, succeeded: bool) -> dict[str, Any]:
        return {
            "checked": self.checked,
            "verified_count": sum(c.verified for c in self.classes.values()),
            "unsupported_count": sum(c.unsupported for c in self.classes.values()),
            "unverifiable_count": sum(c.unverifiable for c in self.classes.values()),
            "unsupported": sorted(f.identifier for f in self.blocking),
            "correction_attempted": attempted,
            "correction_succeeded": succeeded,
            "structured_citations": self.resolved_handles,
            "unknown_handles": sorted(self.unknown_handles),
            "classes": {name: report.to_dict() for name, report in self.classes.items()},
        }


# --------------------------------------------------------------------------- #
# reasoning-score claims
# --------------------------------------------------------------------------- #

# MondayOS computes confidence, evidence strength and execution risk. A model may
# explain those numbers; it may not invent new ones. On one hosted turn the
# assessment computed `confidence=0.47` and the answer said:
#
#     **Recommendation confidence: 76% (high)** -- the assessment did not compute
#     this number, but the reasoning is straightforward
#
# The disclaimer is honest and beside the point: the same answer also wrote
# "Recommendation confidence: 47% (medium)" for values that *were* computed, in
# the same format, so a reader cannot tell which number came from the product.
# Computed-not-generated is the architecture; this enforces it.
_SCORE_CONCEPT = {
    "confidence": "confidence",
    "recommendation confidence": "confidence",
    "overall confidence": "confidence",
    "evidence strength": "evidence_strength",
    "evidence_strength": "evidence_strength",
    "execution risk": "execution_risk",
    "execution_risk": "execution_risk",
}

# Only a number *attached* to the concept is a claim. The connectors are a closed
# list on purpose: a percentage reached through arbitrary prose is a different
# quantity that happens to sit near the word. "concentrates risk -- holding 36% of
# the codebase" is a share of the codebase, and "holding" is not a connector.
_SCORE_CLAIM = re.compile(
    r"\b(?P<label>recommendation\s+confidence|overall\s+confidence|confiden(?:ce|t)"
    r"|evidence[\s_]+strength|execution[\s_]+risk)\b"
    # ` \t` rather than `\s`: the gap must not cross a line. It did, and because
    # blank lines and a `---` rule are made of nothing but permitted characters,
    # "## Evidence Strength" reached a number two paragraphs later -- a proximity
    # match dressed as a line-local one, and the exact association heading scope
    # exists to make explicit.
    r"(?P<gap>(?:[ \t:=~\u2248(\[\-\u2013\u2014*_,]|\b(?:is|of|at|was|rated|scored|about"
    r"|approximately|around|high|medium|low|moderate|strong|weak|elevated|the|a|an"
    r"|assessment|mondayos|says|said|puts|put|rates|reports|computed|score"
    r"|value|level|sits|stands|remains|currently)\b){0,30}?)"
    r"(?P<value>\d{1,3}(?:\.\d+)?)\s*(?P<pct>%)?",
    re.I,
)

# The closed list is high-precision and, on its own, too narrow. A live Anthropic
# run wrote "My recommendation confidence for prioritizing the acceptance fix
# instead: 75%" -- a plainly invented score that the connector list could not
# reach, because "for prioritizing the acceptance fix" is not a connector.
#
# A colon closes that gap without reopening D-4. Prose that merely mentions a
# score and later contains a number almost never punctuates it that way, and the
# value must still be score-shaped, so "The risk here: we have 3 open PRs" is a
# count rather than an execution risk of 0.03.
_SCORE_COLON = re.compile(
    r"\b(?P<label>recommendation\s+confidence|overall\s+confidence|confiden(?:ce|t)"
    r"|evidence[\s_]+strength|execution[\s_]+risk)\b"
    r"(?P<gap>[^.:\n]{0,60})"
    r":\s*[*_\s]*(?:about\s+|approximately\s+|around\s+|roughly\s+)?"
    r"(?P<value>\d{1,3}(?:\.\d+)?)\s*(?P<pct>%)?",
    re.I,
)

# The same claim with the words the other way round: "The 75% confidence reflects
# that...". Only whitespace and markup may separate them -- a number further away
# than that is a different quantity, which is what the gap rules above are for.
_SCORE_AFTER = re.compile(
    # Same line only. Across a newline the next label belongs to the next row of
    # a table or list -- "0.61\n**Evidence Strength**: 0.88" would otherwise read
    # the confidence as an evidence strength.
    r"(?P<value>\d{1,3}(?:\.\d+)?)[ \t]*(?P<pct>%)?[)*_ \t]*"
    r"(?:overall\s+|recommendation\s+|computed\s+|own\s+)?"
    r"\b(?P<label>confiden(?:ce|t)|evidence[\s_]+strength|execution[\s_]+risk)\b",
    re.I,
)

# A number that says what it is a share *of* is not a score. Same discriminator
# the acceptance extractor uses, for the same reason.
_SHARE = re.compile(r"^\s*%?\s*of\b", re.I)

# --------------------------------------------------------------------------- #
# heading scope (D-9)
# --------------------------------------------------------------------------- #
#
# The three shapes above are line-local: each needs its label and its number
# within one line or one short span. A live answer used neither --
#
#     ## Evidence Strength
#
#     - **Integrations recommendation**: 94% (high)
#     - **Workspace and projects recommendations**: 95% (high)
#
# -- where the *heading* establishes the concept and the bullets beneath inherit
# it. No line-local reader can see that, so the 95% was never recognised as a
# claim at all. It happened to be a computed value; an invented number in the
# same position would have been delivered unchecked.
#
# This is structural parsing, deliberately, rather than another widened window. A
# heading either establishes exactly one concept or it establishes none, and its
# block ends at the next heading, a horizontal rule, a fenced code block, or the
# end of the document. Nothing is inferred across sections.

_HEADING_LINE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_RULE_LINE = re.compile(r"^\s{0,3}([-*_])\s*(?:\1\s*){2,}$")
_FENCE_LINE = re.compile(r"^\s{0,3}(?:```|~~~)")

# A number that could be a score at all: a percentage, or a decimal on the 0-1
# scale. A bare integer inside a scored block is a count -- "File count: 61" is
# not an evidence strength of 0.61, and inheriting a concept must not change what
# counts as a number in the first place.
_SCOPED_VALUE = re.compile(r"(?<![\w.])(?P<value>\d{1,3}(?:\.\d+)?)\s*(?P<pct>%)?")


def _heading_concept(title: str) -> str | None:
    """
    The single reasoning concept a heading establishes, or nothing.

    Exactly one, by design. `## Confidence and Evidence` names a section, not a
    score, and a heading that could mean two things means neither -- guessing
    which is how a confidence became an evidence strength in the first place.
    """
    plain = re.sub(r"[^a-z\s_]+", " ", title.lower())
    plain = re.sub(r"[\s_]+", " ", plain).strip()
    found = {
        concept
        for label, concept in _SCORE_CONCEPT.items()
        if re.search(rf"\b{re.escape(label)}\b", plain)
    }
    return found.pop() if len(found) == 1 else None


def _heading_scoped_claims(text: str) -> list[tuple[int, str, float, str]]:
    """Every score-shaped number inside a block whose heading named one concept."""
    found: list[tuple[int, str, float, str]] = []
    scope = ""
    fenced = False
    at = 0
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\n")
        if _FENCE_LINE.match(body):
            # A fence both ends the scope and hides what it contains. Numbers in
            # a code block are code, whatever section they sit in.
            fenced = not fenced
            scope = ""
        elif fenced:
            pass
        elif heading := _HEADING_LINE.match(body):
            scope = _heading_concept(heading.group(2)) or ""
        elif _RULE_LINE.match(body):
            scope = ""
        elif scope:
            for match in _SCOPED_VALUE.finditer(body):
                if not match.group("pct") and "." not in match.group("value"):
                    continue
                if _SHARE.match(body[match.end("value") :]):
                    continue
                raw = float(match.group("value"))
                value = raw / 100.0 if match.group("pct") or raw > 1.0 else raw
                found.append((at + match.start("value"), scope, round(value, 4), body.strip()))
        at += len(line)
    return found


# A second number offered *against* MondayOS's own: "the assessment says 47%, but
# I think it's really 70%". The first number is a faithful report and the second
# is the model substituting its own judgement, which is the same defect as
# inventing one outright -- and it would otherwise escape, because the label sits
# on the number the model is disagreeing with.
# A full stop, as opposed to a decimal point: a period that actually ends a
# sentence is followed by whitespace or nothing.
_SENTENCE_END = re.compile(r"\.(?=\s|$)")

_OVERRIDE = re.compile(
    r"\b(?:but|however|although|though|really|actually|personally|instead"
    r"|i\s+(?:think|reckon|would\s+(?:say|put|call)|'d\s+(?:say|put|call))"
    r"|my\s+own)\b[^\n]{0,60}?"
    r"(?P<value>\d{1,3}(?:\.\d+)?)\s*(?P<pct>%)?",
    re.I,
)

# How close a quoted number must be to an authoritative one. Half a percentage
# point: "47%" and "0.47" are the same value, "46.9%" is a rounding of it, and
# 76% is not a rounding of anything MondayOS produced. Stated as a constant
# because "reasonable rounding" has to mean one specific thing to be testable.
SCORE_TOLERANCE = 0.005


@dataclass(frozen=True)
class ScoreClaim:
    """One numeric reasoning score an answer attributed to MondayOS."""

    concept: str
    value: float
    text: str
    supported: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "concept": self.concept,
            "value": self.value,
            "text": self.text,
            "supported": self.supported,
        }


@dataclass(frozen=True)
class ScoreReport:
    """What score validation concluded about one answer."""

    claims: list[ScoreClaim] = field(default_factory=list)
    authoritative: tuple[float, ...] = ()

    @property
    def unsupported(self) -> list[ScoreClaim]:
        return [c for c in self.claims if not c.supported]

    @property
    def checked(self) -> bool:
        """Whether anything was actually judged."""
        return bool(self.claims)

    def to_metadata(self, *, attempted: bool, succeeded: bool) -> dict[str, Any]:
        return {
            "checked": self.checked,
            "claims_found": len(self.claims),
            "verified": len(self.claims) - len(self.unsupported),
            "unsupported": len(self.unsupported),
            # Every claim, not only the failures. A caller auditing this decision
            # needs to know which numbers were *seen*: "the product found no
            # claim here" and "the product found it and cleared it" are different
            # statements, and only one of them is a reason to look closer.
            "claims": [c.to_dict() for c in self.claims],
            "unsupported_claims": [c.to_dict() for c in self.unsupported],
            "authoritative": list(self.authoritative),
            "correction_attempted": attempted,
            "correction_succeeded": succeeded,
        }


def extract_score_claims(answer: str) -> list[tuple[str, float, str]]:
    """
    Every number an answer attaches to one of MondayOS's reasoning concepts.

    Returns `(concept, value, phrase)`. A percentage is normalised to a fraction,
    so `47%` and `0.47` are one value.

    Three shapes, because one was demonstrably not enough. A label with a short
    closed-list connector (`Confidence: 47%`); a label with any short qualifying
    phrase and a colon (`recommendation confidence for the acceptance fix: 75%`);
    and the number first (`the 75% confidence reflects...`). Each is narrow on a
    different axis, and a claim found by more than one is counted once.

    Numbers that merely sit near the word "risk" are still not claims -- see
    `_SCORE_CLAIM` for why the connectors are a closed list, and `_SHARE` for the
    discriminator that keeps "36% of the codebase" out of it.
    """
    text = canonical(answer)
    # Keyed by where the number is, so the same claim reached by two patterns is
    # one claim. Counting it twice would report an answer as making more score
    # claims than it does, which is the kind of quiet inflation that makes a
    # validation metric untrustworthy.
    found: dict[int, tuple[str, float, str]] = {}

    def concept_of(label: str) -> str | None:
        name = re.sub(r"[\s_]+", " ", label.lower()).strip()
        return _SCORE_CONCEPT.get(name) or ("confidence" if "confiden" in name else None)

    def as_fraction(raw: float, pct: str | None) -> float:
        # A bare number above 1 under a score label is not on MondayOS's scale at
        # all; treat it as a percentage rather than silently accepting an
        # out-of-range score.
        return raw / 100.0 if pct or raw > 1.0 else raw

    def record(match: re.Match[str], *, shaped: bool = False) -> None:
        if _SHARE.match(text[match.end("value") :]):
            return
        if shaped and not (match.group("pct") or "." in match.group("value")):
            return
        concept = concept_of(match.group("label"))
        if concept is None:
            return
        value = as_fraction(float(match.group("value")), match.group("pct"))
        found[match.start("value")] = (concept, round(value, 4), match.group(0).strip())

    for match in _SCORE_CLAIM.finditer(text):
        record(match)

        # Anything the model offers in place of that number, in the same
        # sentence, is a claim about the same concept.
        #
        # The sentence ends at a full stop, which is not the same thing as the
        # next "." character: `find(".")` stopped inside `0.71` and handed the
        # rebuttal check a value of `0`, so "Confidence was 0.34, now I would say
        # 0.71" reported only the legitimate number and let the invented one
        # through -- the exact class of miss this validation exists to prevent.
        concept = concept_of(match.group("label"))
        stop = _SENTENCE_END.search(text, match.end())
        tail_at = match.end()
        tail = text[tail_at : stop.start() if stop else len(text)]
        rebuttal = _OVERRIDE.search(tail)
        # Score-shaped only: a percentage, or a decimal on MondayOS's 0-1 scale.
        # A bare integer is a count -- "but only 12 files are covered" is not a
        # competing confidence of 0.12.
        if (
            concept
            and rebuttal
            and (rebuttal.group("pct") or "." in rebuttal.group("value"))
            and not _SHARE.match(tail[rebuttal.end("value") :])
        ):
            other = as_fraction(float(rebuttal.group("value")), rebuttal.group("pct"))
            found[tail_at + rebuttal.start("value")] = (
                concept,
                round(other, 4),
                rebuttal.group(0).strip(),
            )

    for match in _SCORE_COLON.finditer(text):
        record(match, shaped=True)
    for match in _SCORE_AFTER.finditer(text):
        record(match, shaped=True)

    # Last, and never overriding. A line that names its own concept has said what
    # it means; the heading only speaks for numbers that would otherwise have no
    # label at all. Under `## Confidence and Evidence`, the line
    # `**Evidence Strength:** 0.88` keeps its own concept.
    for at, concept, value, phrase in _heading_scoped_claims(text):
        found.setdefault(at, (concept, value, phrase))

    return [found[at] for at in sorted(found)]


def validate_scores(answer: str, authoritative: Sequence[float]) -> ScoreReport:
    """
    Numeric reasoning scores in an answer, judged against what MondayOS produced.

    The model may explain a score, round it, or describe it in words. It may not
    introduce one. With no authoritative values for the turn -- a grounded
    question that recalled no strategic state -- any attributed number is
    unsupported, because there is nothing it could be reporting.
    """
    allowed = tuple(round(float(v), 4) for v in authoritative)
    claims = [
        ScoreClaim(
            concept=concept,
            value=value,
            text=phrase,
            supported=any(abs(value - a) <= SCORE_TOLERANCE for a in allowed),
        )
        for concept, value, phrase in extract_score_claims(answer)
    ]
    return ScoreReport(claims=claims, authoritative=allowed)


# --------------------------------------------------------------------------- #
# claim extraction
# --------------------------------------------------------------------------- #

# Identifier shapes. Widened from the originals after the red-team review found
# five-digit ADRs, seven-digit tasks, hyphenated PR forms and unlisted file
# extensions all slipping through unexamined.
# Boundaries are spelled out rather than using `\b`, because `_` is a word
# character: `\b` never fires inside `_6d23456_`, so Markdown underscore emphasis
# hid an identifier from every pattern at once. A generated corpus found this in
# its first run; no hand-written list of delimiters had.
_EDGE = r"(?<![0-9A-Za-z])"
_EDGE_END = r"(?![0-9A-Za-z])"

# Upper bound 64, not 40. A git SHA is at most 40 characters, so an overlong hex
# run cannot be one -- but an independent verifier caught a 55-character forgery
# being delivered in backticks as a commit, because "not a valid SHA" and "not a
# claim" had been treated as the same thing. Git rejects it; the answer fails
# closed. The cost is that an answer quoting a long hex digest -- a lockfile
# checksum, say -- is now a claim too, and will fail closed if it does not
# resolve. That is the safe direction: refusing to vouch for a digest is
# recoverable, presenting a fabricated one as fact is not.
_SHA_LIKE = re.compile(rf"{_EDGE}([0-9a-f]{{6,64}}){_EDGE_END}", re.I)
_DECISION_LIKE = re.compile(rf"{_EDGE}((?:ADR|DEC)[-_ #]?\d{{1,6}}){_EDGE_END}", re.I)
_TASK_LIKE = re.compile(rf"{_EDGE}(TASK[-_ #]?\d{{1,8}}){_EDGE_END}", re.I)
_PR_LIKE = re.compile(r"(?:\b(?:PR|pull[- ]request)[-_ ]*#?\s?(\d{1,6})\b|/pull/(\d{1,6})\b)", re.I)
_PATH_LIKE = re.compile(rf"{_EDGE}((?:[\w.\-]+/)+[\w.\-]+\.[A-Za-z][\w]{{0,9}}){_EDGE_END}")
# Symbols are deliberately absent from the heuristic extractor. Every backticked
# word in an answer -- `git status`, `main`, `README` -- would be a candidate, and
# a project index that does not define them would then report each as fabricated.
# That trades a missed-fabrication risk for a false-accusation certainty, which is
# the wrong direction. Symbol claims are recognised only through the structured
# citation protocol, where the kind is stated rather than guessed, and the
# per-class report says so instead of implying symbols were scanned.

_DASHES = {ord(c): "-" for c in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"}
# Markdown's own escape. `6d2\3456` renders as `6d23456`, so a validator reading
# the raw text sees a token the reader never will -- and misses the one they do.
_ESCAPE = re.compile(r"\\(.)")
# Markup a reader never sees. Stripping it is the same rule as decoding an
# entity: `<b>7845950</b>` shows a reader a plain identifier, and leaving the
# tags in place hid every all-digit hash wrapped in HTML from the position test.
_TAG = re.compile(r"<!--.*?-->|<[^>\n]{0,200}>", re.S)

# Characters that mark a token as a citation rather than a quantity. A run of any
# of these on both sides is a citation marker: markdown emphasis and code spans,
# quotes, brackets, and table cells. Enumerating *delimiters* is tractable in a
# way that enumerating renderings is not -- `**7654321**` defeated a rule built
# on bullet positions alone.
_OPEN = "`*_~\"'([{|<"
_CLOSE = "`*_~\"')]}|>"


def canonical(text: str) -> str:
    """
    The answer as a reader will see it, so a lookalike cannot hide in the markup.

    The rule this enforces is that **validation and display must agree on what
    the identifier is**. An independent verifier found three ways they did not:
    `6d2\3456` (Markdown escape), `ADR&#45;9099` (HTML entity) and a soft hyphen
    inside a hash all render as valid identifiers while the raw text hides them
    from every pattern at once.

    So the raw text is put through what a renderer does before anything is
    extracted: entities decoded, escapes dropped, format characters removed,
    compatibility forms folded. Invisible characters are removed by Unicode
    *category* rather than by an enumerated list -- the list is what the soft
    hyphen walked through, and `Cf` covers the zero-width family, the bidi marks
    and the byte-order mark together.
    """
    decoded = html.unescape(_TAG.sub(" ", text))
    folded = unicodedata.normalize("NFKC", decoded)
    stripped = "".join(c for c in folded if unicodedata.category(c) != "Cf")
    return _ESCAPE.sub(r"\1", stripped).translate(_DASHES)


def _delimited(text: str, start: int, end: int) -> bool:
    """Whether a token is wrapped in citation punctuation on both sides."""
    left = start
    while left > 0 and text[left - 1] in _OPEN and start - left < 3:
        left -= 1
    right = end
    while right < len(text) and text[right] in _CLOSE and right - end < 3:
        right += 1
    return left < start and right > end


def _is_commit_reference(answer: str, start: int, end: int, candidate: str) -> bool:
    """
    Whether a hex token is being *used* as a commit reference.

    A token containing a hex letter is unambiguous -- no ordinary word is seven
    hex characters by accident. An all-digit token is not: "we processed 1234567
    records" contains one. Those count when wrapped in citation punctuation, or
    when standing where a commit stands: the start of a line or list item,
    optionally behind a label.

    The delimiter test is deliberately broad, and the cost is understood: a bare
    parenthesised seven-digit quantity will be read as a claim and, failing to
    resolve, will force a regeneration. That is the safe direction of error, and
    the structured citation protocol is what removes the need to guess at all --
    a compliant answer cites `[E1]` and never puts MondayOS in the position of
    inferring intent from punctuation.
    """
    if any(c in "abcdef" for c in candidate.lower()):
        return True
    if _delimited(answer, start, end):
        return True
    prefix = answer[answer.rfind("\n", 0, start) + 1 : start]
    if not _CITATION_POSITION.match(prefix):
        return False
    following = answer[end : end + 2]
    return following == "" or bool(re.match(r"[\s:`,)\].]", following))


# Only scaffolding may stand between the line start and an all-digit hash:
# indentation, a quote marker, a bullet, an ordinal, a table cell, an explicit
# label, an opening delimiter.
_CITATION_POSITION = re.compile(
    r"""^[ \t>|]*(?:[-*+]|\d{1,3}[.)])?[ \t|]*"""
    r"""(?:(?:commit|sha|hash|revision|rev)[ \t]*[:#=-]?[ \t]*)?[`"'*_~]{0,3}$""",
    re.I,
)


def extract_claims(answer: str) -> list[tuple[str, str]]:
    """
    Every identifier-shaped claim an answer makes, as ``(kind, identifier)``.

    Extraction is unconditional. It used to run per class only when retrieval had
    supplied that class, so an answer inventing commits on a turn whose git
    source had been trimmed produced no claims at all and was reported clean.
    What the model was given and what the model asserted are different questions,
    and only the second one is asked here.
    """
    text = canonical(answer)
    claims: list[tuple[str, str]] = []

    for match in _SHA_LIKE.finditer(text):
        candidate = match.group(1)
        if _is_commit_reference(text, match.start(), match.end(), candidate):
            claims.append(("commit", candidate))

    for pattern, kind in ((_DECISION_LIKE, "decision"), (_TASK_LIKE, "task")):
        claims.extend((kind, m.group(1)) for m in pattern.finditer(text))

    for match in _PR_LIKE.finditer(text):
        claims.append(("pull_request", match.group(1) or match.group(2)))

    claims.extend(
        ("file", m.group(1)) for m in _PATH_LIKE.finditer(text) if not _in_url(text, m.start())
    )
    return claims


def _in_url(text: str, start: int) -> bool:
    """
    Whether a path-shaped match is part of a web address.

    `example.com/docs/guide.html` is not a claim about this repository, and
    treating it as one would fail a turn closed for citing a website.
    """
    left = text.rfind(" ", 0, start) + 1
    right = text.find(" ", start)
    word = text[left : right if right != -1 else len(text)]
    return "://" in word or word.lower().startswith("www.")


# What a citation calls itself, and what the validator calls the same thing.
_HANDLE_KIND = {
    "commit": "commit",
    "file": "file",
    "test": "file",
    "decision": "decision",
    "task": "task",
    "pull-request": "pull_request",
    "pull_request": "pull_request",
    "symbol": "symbol",
}


def validate_evidence(
    answer: str,
    evidence: EvidenceSet,
    authority: EvidenceAuthority | None = None,
    extra_claims: list[tuple[str, str]] | None = None,
) -> EvidenceReport:
    """
    Every identifier-shaped claim in an answer, resolved against two authorities.

    Deterministic and model-free by construction. Asking the model whether its own
    citation is real would be asking the thing that invented it, and a
    hallucinated SHA is exactly as confidently produced as a true one.

    Resolution order, strongest first:

    1. **Retrieval supplied it.** The model was handed this exact identifier, so
       citing it is quotation rather than invention.
    2. **The project's own records resolve it.** Git resolves abbreviations and
       rejects both forged and ambiguous revisions; record stores answer for
       decisions and tasks; the filesystem answers for paths.
    3. **Neither could answer.** ``UNVERIFIABLE`` -- never silently clean.

    Without an authority, every claim retrieval did not supply is unverifiable.
    That is the honest reading of a validator with nothing to check against, and
    it is why the service always supplies one.
    """
    checker: EvidenceAuthority = authority or NullAuthority()
    try:
        can = checker.available()
    except Exception:  # noqa: BLE001 — a broken authority is unavailable, not fatal
        can = {}
    counts = {name: [0, 0, 0, 0] for name in CLASSES}
    findings: list[EvidenceFinding] = []
    seen: set[tuple[str, str]] = set()

    for kind, identifier in list(extra_claims or []) + extract_claims(answer):
        if kind not in counts:
            continue
        key = (kind, identifier.lower())
        if key in seen:
            continue
        seen.add(key)
        verdict = _resolve(kind, identifier, evidence, checker)
        counts[kind][0] += 1
        counts[kind][
            1
            if verdict is EvidenceVerdict.VERIFIED
            else 2
            if verdict is EvidenceVerdict.UNSUPPORTED
            else 3
        ] += 1
        findings.append(EvidenceFinding(kind, identifier, verdict))

    classes = {
        name: ClassReport(
            claims_found=counts[name][0],
            verified=counts[name][1],
            unsupported=counts[name][2],
            unverifiable=counts[name][3],
            authority_available=bool(can.get(_AUTHORITY_KEY.get(name, name), False)),
        )
        for name in CLASSES
    }
    return EvidenceReport(findings=findings, classes=classes)


# `file` is the claim kind; `path` is what an authority calls it.
_AUTHORITY_KEY = {"file": "path"}


def _resolve(
    kind: str,
    identifier: str,
    evidence: EvidenceSet,
    authority: EvidenceAuthority,
) -> EvidenceVerdict:
    """
    One claim, against the project first and retrieval only as a fallback.

    The order matters and was the other way round. Retrieval used to be able to
    vouch for an identifier the project itself denies -- so a bug anywhere in the
    citation path could launder a non-existent commit into a verified one, and
    the authority would never be asked. The project's own records are the
    stronger claim, so they are consulted first and can overrule.

    Retrieval still settles the case the records cannot answer: with no git, no
    record store or no index, "the model was handed this exact identifier"
    remains real evidence, and rejecting it would fail turns closed for citing
    what MondayOS itself supplied.
    """
    lookup = {
        "commit": authority.commit,
        "decision": authority.decision,
        "task": authority.task,
        "pull_request": authority.pull_request,
        "file": authority.path,
        "symbol": authority.symbol,
    }[kind]
    try:
        resolution = lookup(identifier)
    except Exception:  # noqa: BLE001 — see below
        # A misbehaving authority must degrade to "could not check", never take
        # the turn down. The fail-closed matrix found this raising straight out
        # of validation, which loses the user's turn instead of refusing it.
        resolution = Resolution.UNAVAILABLE
    if resolution is Resolution.RESOLVED:
        return EvidenceVerdict.VERIFIED
    if resolution is Resolution.UNKNOWN:
        # The project says no. Retrieval does not get a vote here.
        return EvidenceVerdict.UNSUPPORTED
    return (
        EvidenceVerdict.VERIFIED
        if evidence.holds(kind, identifier)
        else EvidenceVerdict.UNVERIFIABLE
    )


def resolve_handles(
    answer: str, handles: dict[str, EvidenceHandle]
) -> tuple[str, list[EvidenceHandle], list[str]]:
    """
    Replace cited handles with the identifiers they stand for.

    The structured path is the authoritative one: `[E1]` either names a fact
    retrieval supplied or it names nothing, and there is no third reading for a
    parser to get wrong. Substitution happens before the heuristic backstop runs,
    so a compliant answer arrives at validation already carrying real
    identifiers, and an answer citing `[E9]` when eight handles exist is caught
    as a fabricated reference rather than rendered to the user as one.
    """
    unknown: list[str] = []
    cited: list[EvidenceHandle] = []

    def swap(match: re.Match[str]) -> str:
        labels = [m.group(0).upper() for m in _HANDLE_MEMBER.finditer(match.group(1))]
        resolved = [handles.get(label) for label in labels]
        missing = [label for label, h in zip(labels, resolved, strict=True) if h is None]
        if missing:
            # One unknown handle invalidates the whole group. Rendering the rest
            # would show a citation that is partly real and partly not, which is
            # harder to distrust than one that is plainly wrong.
            unknown.extend(missing)
            return match.group(0)
        seen: list[EvidenceHandle] = []
        for handle in resolved:
            assert handle is not None
            cited.append(handle)
            if handle.reference not in [h.reference for h in seen]:
                seen.append(handle)
        return ", ".join(h.reference for h in seen)

    return HANDLE.sub(swap, answer), cited, unknown


def _correction_prompt(
    request: WorkspaceRequest,
    blocking: list[EvidenceFinding],
    bad_scores: Sequence[ScoreClaim] = (),
) -> str:
    """
    A correction that states the finding rather than asking for one.

    MondayOS already knows which identifiers failed, so the model is told, not
    consulted. Asking "are these citations valid?" would put the question to the
    only party with no way to answer it.
    """
    unsupported = sorted(f.identifier for f in blocking if f.verdict is EvidenceVerdict.UNSUPPORTED)
    unverifiable = sorted(
        f.identifier for f in blocking if f.verdict is EvidenceVerdict.UNVERIFIABLE
    )
    lines = []
    if bad_scores:
        named = ", ".join(f"{c.concept} {c.value}" for c in bad_scores)
        allowed = ", ".join(str(v) for v in request.authoritative_scores) or "none"
        lines.append(
            f"These reasoning scores are not MondayOS's: {named}. MondayOS computed "
            f"{allowed} for this turn. Report only those numbers, or describe your "
            "judgement in words without attaching a number to it."
        )
    if unsupported:
        lines.append(
            "These identifiers do not exist in this project's records: "
            f"{', '.join(unsupported)}. MondayOS checked and they are not real."
        )
    if unverifiable:
        lines.append(
            "These identifiers could not be verified against this project: "
            f"{', '.join(unverifiable)}. Do not present them as established fact."
        )
    return (
        f"{request.instruction()}\n\n"
        + "\n".join(lines)
        + "\n\nAnswer again, citing only the supplied evidence handles for repository "
        "evidence. Do not repeat the identifiers above and do not replace them with "
        "new ones. If the evidence does not cover part of the question, say so plainly."
        f"\n\n{request.message}"
    )


def _normalise_id(raw: str) -> str:
    """
    `adr 17`, `ADR-17` and `ADR-017` are one reference written three ways.

    Zero-padding is stripped rather than imposed. Padding to a fixed width turned
    `TASK-0059` into `TASK-059` and made a real task look invented, which is the
    failure direction this whole validator exists to avoid.
    """
    match = re.match(r"([A-Za-z]+)[-_ #]?(\d+)", raw.strip())
    if not match:
        return raw.upper()
    return f"{match.group(1).upper()}-{int(match.group(2))}"


@dataclass
class WorkspaceRequest:
    """
    One turn's worth of everything a responder could route on.

    Deliberately structured: a future router reads ``snapshot`` size, ``project``,
    and message shape to choose a model. A pre-rendered string would hide all of it.
    """

    project: str
    message: str
    snapshot: ContextSnapshot | None = None
    history: list[Message] = field(default_factory=list)
    conversation_id: str = ""
    # 0 means "let the responder decide from the register". A concrete value is a
    # caller overriding that, usually because it knows what it can display.
    max_tokens: int = 0
    # A deterministic condensation of turns older than the verbatim window. Empty
    # for a short conversation. Carried separately from ``history`` so a router
    # can see that a thread was compacted rather than inferring it from length.
    history_digest: str = ""
    # What MondayOS concluded before the model was called. Carried as a value
    # rather than folded into the context text for the same reason the snapshot
    # is: a router needs to see that a question was routed as strategic, and
    # flattening it into a prompt string would hide the one field that says so.
    assessment: Assessment | None = None
    # The project answering about its own records. Separate from the snapshot on
    # purpose: the context budget decides what the model is *shown*, and it must
    # not decide what MondayOS can *verify*. Absent it, every claim retrieval did
    # not supply is unverifiable rather than clean.
    authority: EvidenceAuthority | None = None
    # Every numeric reasoning score MondayOS computed or persisted for this turn.
    # Supplied by the service, which is the only layer that can see both the
    # freshly computed assessment and the strategic state a continuation is
    # narrating. Empty means the turn has no score authority, and any number the
    # answer attributes to a reasoning concept is therefore unsupported.
    authoritative_scores: tuple[float, ...] = ()

    @property
    def executive(self) -> bool:
        """Whether this turn is in the strategic register."""
        return self.assessment is not None and self.assessment.mode is Mode.EXECUTIVE

    @property
    def continuation(self) -> bool:
        """Whether this turn continues a decision already made."""
        return self.assessment is not None and bool(getattr(self.assessment, "continuation", False))

    def instruction(self) -> str:
        """
        The standing instruction this turn should run under.

        Three instructions, two modes. A continuation is executive for every
        purpose that matters downstream — register, budget, structure — so it
        does not need a Mode of its own; what it needs is to be told not to
        re-decide, which is an instruction concern rather than a routing one.
        """
        if self.continuation:
            return CONTINUATION_INSTRUCTION
        return EXECUTIVE_INSTRUCTION if self.executive else SYSTEM_INSTRUCTION

    def handles(self) -> dict[str, EvidenceHandle]:
        """
        The retrieved facts, each addressable by an opaque label.

        Labels are positional (`E1`, `E2`) and carry no information a model could
        use to construct a plausible variant -- which is the whole point. A model
        can invent a seven-character hash that looks exactly like a real one; it
        cannot invent `[E4]` into existence when only three handles were issued.
        """
        if self.snapshot is None:
            return {}
        handles: dict[str, EvidenceHandle] = {}
        for index, citation in enumerate(self.snapshot.citations, start=1):
            reference = str(citation.get("reference", "") or "").strip()
            if not reference:
                continue
            label = f"E{index}"
            handles[label] = EvidenceHandle(
                label=label,
                kind=str(citation.get("kind", "") or "reference"),
                reference=reference,
                description=str(citation.get("label", "") or "").strip(),
            )
        return handles

    def render_handles(self) -> str:
        """The handle block the model is asked to cite from."""
        handles = self.handles()
        if not handles:
            return ""
        lines = "\n".join(h.render() for h in handles.values())
        return f"EVIDENCE HANDLES (cite these, never a hand-written identifier):\n{lines}"

    @property
    def validates(self) -> bool:
        """
        Whether this turn can have its claims checked at all.

        Buffering keys off this rather than off the evidence set. Streaming a turn
        because retrieval happened to supply nothing is how an answer with
        invented identifiers reached a reader before anything looked at it.

        Score authority counts for the same reason. An invented confidence is as
        unrecallable once displayed as an invented commit hash.
        """
        return self.authority is not None or not self.evidence().empty or self.checks_scores

    @property
    def checks_scores(self) -> bool:
        """
        Whether this turn's numbers are MondayOS's to account for.

        A turn MondayOS reasoned about carries an assessment, and every reasoning
        score in the answer then has to trace to something computed or persisted
        -- including when the authoritative set is empty, which is precisely the
        case where a number cannot be reporting anything.

        Ordinary conversation is left alone. A turn with no assessment and no
        stored decision is not making claims about MondayOS's reasoning, and
        treating "I am 90% sure" in a chat as a fabricated score would be the
        false-accusation failure in a new place.
        """
        return self.assessment is not None or bool(self.authoritative_scores)

    def evidence(self) -> EvidenceSet:
        """
        Everything this turn is allowed to cite, from what it actually received.

        Built from the snapshot's structured citations -- the same objects
        retrieval produced, kept alongside the prose the model reads rather than
        parsed back out of it. A turn with no snapshot, or a snapshot with no
        citations, yields an empty set and validates nothing: a turn that was
        given no evidence cannot have misused any.
        """
        if self.snapshot is None:
            return EvidenceSet()

        commits: set[str] = set()
        decisions: set[str] = set()
        tasks: set[str] = set()
        pulls: set[str] = set()
        paths: set[str] = set()
        symbols: set[str] = set()

        for citation in self.snapshot.citations:
            kind = str(citation.get("kind", ""))
            reference = str(citation.get("reference", "") or "").strip()
            path = str(citation.get("path", "") or "").strip()
            if path:
                paths.add(path)
            if not reference:
                continue
            if kind == "commit":
                commits.add(reference.lower())
            elif kind == "decision":
                decisions.add(_normalise_id(reference))
            elif kind == "task":
                tasks.add(_normalise_id(reference))
            elif kind == "pull-request":
                pulls.add(reference.lstrip("#"))
            elif kind == "symbol":
                symbols.add(reference)
            elif kind in ("file", "test"):
                paths.add(reference)

        return EvidenceSet(
            commits=frozenset(commits),
            decisions=frozenset(decisions),
            tasks=frozenset(tasks),
            pull_requests=frozenset(pulls),
            paths=frozenset(paths),
            symbols=frozenset(symbols),
        )

    def token_budget(self, default: int = DEFAULT_MAX_TOKENS) -> int:
        """
        Output budget for this turn.

        A strategic answer needs several times a conversational one. An explicit
        ``max_tokens`` on the request still wins — the caller knows more than we
        do about what it can display.
        """
        if self.max_tokens:
            return self.max_tokens
        return EXECUTIVE_MAX_TOKENS if self.executive else default

    def render_context(self, history_turns: int = DEFAULT_HISTORY_TURNS) -> str:
        """
        Flatten snapshot and history into provider context text.

        Called by a responder at the last moment, never before: the structure is
        what makes routing possible, so it is preserved right up to the call.
        """
        blocks: list[str] = []
        if self.assessment is not None and (self.assessment.has_reasoning or self.assessment.facts):
            # Placed before the raw snapshot deliberately. A model reads what comes
            # first as the frame for what follows, and the assessment is the
            # conclusion the snapshot supports — inverting them invites a summary
            # of documents with the reasoning appended as an afterthought.
            blocks.append("# MondayOS assessment\n" + self.assessment.render())
        if self.snapshot is not None:
            blocks.append(self.snapshot.render())
            # Placed with the evidence it labels, so the model sees the handle
            # and the fact together rather than being asked to match them up.
            blocks.append(self.render_handles())
        if self.history_digest:
            blocks.append(self.history_digest)

        turns = [m for m in self.history if m.role in (MessageRole.USER, MessageRole.ASSISTANT)]
        recent = turns[-history_turns:] if history_turns > 0 else []
        if recent:
            lines = ["# Conversation so far"]
            for message in recent:
                speaker = "User" if message.role is MessageRole.USER else "MondayOS"
                lines.append(f"{speaker}: {message.content}")
            blocks.append("\n".join(lines))

        return "\n\n".join(b for b in blocks if b)


@dataclass
class WorkspaceReply:
    """
    What a responder produced.

    ``content`` is the visible answer and the only thing persisted as message
    text. ``provider``/``model`` are provenance. There is deliberately no field
    for provider reasoning: it is not requested, and there is nowhere to put it
    if it arrived (ADR-015).
    """

    content: str
    provider: str = ""
    model: str = ""
    tokens_used: int = 0
    error: str = ""
    # True when generation stopped before the model finished — a user pressing
    # stop, or a stream that died mid-answer. The distinction matters: partial
    # text presented as a complete answer is a quiet correctness failure, so the
    # flag travels with the reply and is persisted on the message.
    incomplete: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.content.strip())


@dataclass
class ReplyChunk:
    """
    One increment of a streaming reply, in the workspace's own vocabulary.

    A provider's chunk shape never reaches this far: ``ProviderWorkspaceResponder``
    translates. That is what lets a future router stream from providers with
    completely different wire formats without anything above noticing.
    """

    text: str = ""
    done: bool = False
    reply: WorkspaceReply | None = None


class WorkspaceResponder(Protocol):
    """
    The routing seam.

    An implementation turns a request into a reply. It makes no decision about
    whether the conversation should continue, never persists anything, and never
    reaches into the store.
    """

    def respond(self, request: WorkspaceRequest) -> WorkspaceReply:
        """Answer one turn."""
        ...

    def respond_stream(self, request: WorkspaceRequest) -> Iterator[ReplyChunk]:
        """
        Answer one turn incrementally.

        Yields text chunks, then a final chunk carrying the assembled reply.
        Closing the iterator early is how a caller stops generation: the
        implementation must treat that as a stop, not an error.
        """
        ...

    @property
    def name(self) -> str:
        """Identifier for provenance. Never branched on."""
        ...

    @property
    def streams(self) -> bool:
        """True when this responder emits genuinely incremental chunks."""
        ...


class ProviderWorkspaceResponder:
    """
    Increment 1's responder: one configured MondayOS ``AIProvider``.

    The provider is injected — this class never constructs one, never names a
    vendor, and never reads provider configuration. Replacing it with a router is
    a new class implementing the same protocol, not a change here.
    """

    def __init__(
        self,
        provider: AIProvider,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        history_turns: int = DEFAULT_HISTORY_TURNS,
    ) -> None:
        self._provider = provider
        self._max_tokens = max_tokens
        self._history_turns = history_turns

    @property
    def name(self) -> str:
        return self._provider.name

    @property
    def streams(self) -> bool:
        """Whether the configured provider genuinely emits incremental chunks."""
        return self._provider.supports_streaming

    def respond_stream(self, request: WorkspaceRequest) -> Iterator[ReplyChunk]:
        """
        Stream one turn through the provider.

        Three failures are handled here rather than left to the caller:

        **Stop.** A caller that closes this iterator gets ``GeneratorExit``.
        That is a stop, not an error: whatever text arrived is assembled into a
        reply marked ``incomplete`` and handed back through the final chunk, so
        the partial answer is preserved rather than discarded.

        **Mid-stream failure.** A provider that dies after emitting text has
        still produced something. The reply keeps that text, records the error,
        and is marked incomplete — the alternative is throwing away work the
        user watched arrive.

        **Empty stream.** A stream that yields nothing is a failed turn, not an
        empty answer, for the same reason a blank `ask` response is.
        """
        availability = self._provider.availability()
        if not availability.available:
            yield ReplyChunk(
                done=True,
                reply=WorkspaceReply(
                    content="", provider=self._provider.name, error=availability.instructions()
                ),
            )
            return

        context = request.render_context(self._history_turns)
        prompt = f"{request.instruction()}\n\n{request.message}"
        # Evidence integrity outranks progressive display. A turn that can be
        # checked at all is held back until it has been; only a turn with neither
        # evidence nor an authority -- nothing to check against, and so nothing
        # that could be presented as verified -- still streams. Keying this off
        # the evidence set alone is how an answer with invented identifiers
        # reached a reader on a turn whose git context had been trimmed away.
        buffered = request.validates

        parts: list[str] = []
        model = ""
        provider_name = self._provider.name
        stop_reason = ""
        tokens = 0
        error = ""

        try:
            for chunk in self._provider.stream(
                prompt, context=context, max_tokens=request.token_budget(self._max_tokens)
            ):
                if chunk.done:
                    model = chunk.model or model
                    provider_name = chunk.provider or provider_name
                    tokens = chunk.tokens_used or tokens
                    stop_reason = chunk.stop_reason or stop_reason
                    continue
                if chunk.text:
                    parts.append(chunk.text)
                    # An evidence-bearing turn is buffered until validation has
                    # run. Streaming it would put fabricated identifiers in front
                    # of the reader, and no amount of post-generation checking
                    # un-shows a citation someone has already read. The whole
                    # answer is released once, after it has been verified.
                    if not buffered:
                        yield ReplyChunk(text=chunk.text)
        except GeneratorExit:
            # The caller stopped us. Preserve what arrived and re-raise so the
            # generator closes cleanly; the caller already holds the text it saw.
            raise
        except ProviderError as exc:
            error = str(exc)
        except Exception as exc:  # noqa: BLE001 — a provider bug must surface, not vanish
            error = f"{type(exc).__name__}: {exc}"

        content = "".join(parts).strip()
        if not content and not error:
            error = "The provider returned an empty response."

        # Validation runs on a complete answer, so it can only happen here. For a
        # buffered turn nothing has reached the caller yet and the verified text
        # is emitted in one piece; for an unbuffered one the deltas are already
        # out, which is exactly why evidence-bearing turns are not unbuffered.
        if content and not error:
            checked = self._verified(
                request,
                content,
                provider=provider_name,
                model=model,
                tokens=tokens,
                stop_reason=stop_reason,
            )
            if buffered:
                yield ReplyChunk(text=checked.content)
            yield ReplyChunk(done=True, reply=checked)
            return

        # A run cut off at max_tokens produced a partial answer that reads as a
        # finished one. Without this the flag says complete while the text stops
        # mid-sentence — the precise failure `incomplete` exists to prevent, and
        # the reason ProviderChunk carries stop_reason at all.
        truncated = stop_reason == "max_tokens"

        yield ReplyChunk(
            done=True,
            reply=WorkspaceReply(
                content=content,
                provider=provider_name,
                model=model,
                tokens_used=tokens,
                error=error,
                # Text arrived and then something went wrong: partial, not failed.
                incomplete=bool(error and content) or truncated,
                metadata={"stop_reason": stop_reason} if stop_reason else {},
            ),
        )

    def respond(self, request: WorkspaceRequest) -> WorkspaceReply:
        """
        Answer one turn through the provider.

        Returns a reply carrying ``error`` rather than raising: a provider outage
        is a normal thing that happens mid-conversation, and the turn should be
        recorded and retryable rather than lost to an exception.
        """
        availability = self._provider.availability()
        if not availability.available:
            return WorkspaceReply(
                content="",
                provider=self._provider.name,
                error=availability.instructions(),
            )

        context = request.render_context(self._history_turns)
        prompt = f"{request.instruction()}\n\n{request.message}"

        try:
            response = self._provider.ask(
                prompt,
                context=context,
                max_tokens=request.token_budget(self._max_tokens),
            )
        except ProviderError as exc:
            return WorkspaceReply(content="", provider=self._provider.name, error=str(exc))
        except Exception as exc:  # noqa: BLE001 — a provider bug must surface, not vanish
            return WorkspaceReply(
                content="",
                provider=self._provider.name,
                error=f"{type(exc).__name__}: {exc}",
            )

        content = (response.content or "").strip()
        if not content:
            # An empty body is a failed turn, not an answer. Recording it as an
            # assistant message would put a blank bubble in the transcript and
            # let the conversation continue as though something was said.
            return WorkspaceReply(
                content="",
                provider=response.provider or self._provider.name,
                model=response.model,
                error="The provider returned an empty response.",
            )

        return self._verified(
            request,
            content,
            provider=response.provider or self._provider.name,
            model=response.model,
            tokens=response.tokens_used,
            # The provider reported how generation ended; the streaming path
            # threaded it and this one dropped it on the floor. `send_message`
            # uses *this* path, so a hosted run against a provider that reports a
            # stop reason recorded none on all fifty-two turns -- and the gate
            # that exists to catch a truncated answer being shown as finished
            # could never be exercised at all.
            stop_reason=str(response.metadata.get("stop_reason", "") or ""),
        )

    def _verified(
        self,
        request: WorkspaceRequest,
        content: str,
        *,
        provider: str,
        model: str,
        tokens: int,
        stop_reason: str = "",
    ) -> WorkspaceReply:
        """
        An answer, checked against the evidence its own turn received *and* the
        project's own records.

        MondayOS retrieved eight real commits for one acceptance turn and the
        model reported eight, of which six were invented -- while six real ones
        sat unused in its context. The evidence was complete; the narration
        replaced it.

        Two paths, and the first is the authoritative one. Cited handles resolve
        to the identifiers they stand for, deterministically, with an unknown
        handle caught as a fabricated reference. Whatever the model wrote by hand
        then goes through the heuristic backstop, which exists precisely because
        a model may ignore the protocol.

        Three outcomes. A clean answer is returned. A first answer carrying claims
        that are unsupported *or* unverifiable is regenerated **once**, told
        exactly which ones failed -- MondayOS already knows, so asking the model to
        check its own citations would be asking the thing that invented them. A
        second failure fails closed: the caller gets an explicit statement that the
        evidence could not be verified, and the unproven identifiers never become
        an authoritative message.
        """
        evidence = request.evidence()
        handles = request.handles()
        authority = request.authority

        def assess(text: str) -> tuple[str, EvidenceReport, ScoreReport]:
            resolved, cited, unknown = resolve_handles(text, handles)
            # A cited handle states its own kind, which is the only way a symbol
            # claim is ever recognised: guessing symbols out of prose would
            # accuse every backticked word the index does not define.
            extra = [(_HANDLE_KIND[h.kind], h.reference) for h in cited if h.kind in _HANDLE_KIND]
            report = validate_evidence(resolved, evidence, authority, extra_claims=extra)
            scores = (
                validate_scores(resolved, request.authoritative_scores)
                if request.checks_scores
                else ScoreReport()
            )
            return (
                resolved,
                EvidenceReport(
                    findings=report.findings
                    + [
                        EvidenceFinding("handle", label, EvidenceVerdict.UNSUPPORTED, via="handle")
                        for label in dict.fromkeys(unknown)
                    ],
                    classes=report.classes,
                    unknown_handles=unknown,
                    resolved_handles=len(cited),
                ),
                scores,
            )

        def reply(
            body: str,
            report: EvidenceReport,
            scores: ScoreReport,
            *,
            error: str = "",
            incomplete: bool = False,
            attempted: bool,
            succeeded: bool,
        ) -> WorkspaceReply:
            # Validation is additive to truncation, never a replacement for it.
            # A verified answer that stopped at max_tokens is still a partial one.
            metadata: dict[str, Any] = {
                "evidence_validation": report.to_metadata(attempted=attempted, succeeded=succeeded)
            }
            if request.checks_scores:
                metadata["score_validation"] = scores.to_metadata(
                    attempted=attempted, succeeded=succeeded
                )
            if stop_reason:
                metadata["stop_reason"] = stop_reason
            return WorkspaceReply(
                content=body,
                provider=provider,
                model=model,
                tokens_used=tokens,
                error=error,
                incomplete=incomplete or stop_reason == "max_tokens",
                metadata=metadata,
            )

        shown, first, first_scores = assess(content)
        if not first.blocking and not first_scores.unsupported:
            return reply(shown, first, first_scores, attempted=False, succeeded=False)

        try:
            corrected = self._provider.ask(
                _correction_prompt(request, first.blocking, first_scores.unsupported),
                context=request.render_context(self._history_turns),
                max_tokens=request.token_budget(self._max_tokens),
            )
            retry = (corrected.content or "").strip()
        except Exception:  # noqa: BLE001 — a failed correction must not rescue the original
            retry = ""

        report, score_report = first, first_scores
        if retry:
            shown_again, second, second_scores = assess(retry)
            if not second.blocking and not second_scores.unsupported:
                return reply(shown_again, second, second_scores, attempted=True, succeeded=True)
            report, score_report = second, second_scores

        # Fail closed. The unproven identifiers are reported in metadata for
        # diagnosis and deliberately kept out of the body: repeating them in the
        # answer would put the fabrication back in front of the reader, which is
        # the thing this exists to prevent.
        # Name the check that failed. A reader told the evidence did not resolve,
        # when what actually happened is that the answer invented a confidence
        # score, has been told something false about their own system.
        if report.blocking:
            body = (
                "MondayOS could not verify the evidence cited in this answer, so it has "
                "not been shown. The references did not resolve against anything this "
                "project records. Ask again, or narrow the question to something the "
                "project records directly."
            )
            error = "unverified evidence in generated answer"
        else:
            body = (
                "MondayOS could not verify the reasoning scores in this answer, so it "
                "has not been shown. It attributed a confidence, evidence strength or "
                "execution risk to MondayOS that MondayOS did not compute. Ask again."
            )
            error = "unverified evidence: unsupported reasoning score in generated answer"
        return reply(
            body,
            report,
            score_report,
            error=error,
            incomplete=True,
            attempted=True,
            succeeded=False,
        )
