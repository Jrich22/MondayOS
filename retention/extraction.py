"""Knowledge extraction from provider responses (§2).

After any provider response, MondayOS extracts *candidate* knowledge — it does
not blindly save raw model output. This module segments a response, strips
hidden reasoning, classifies each durable statement into one of the eight
knowledge kinds, and drops everything that must not be stored:

    * casual conversation          * secrets
    * repetitive prose             * raw chain-of-thought
    * temporary status updates      * unsupported speculation

What survives becomes ``KnowledgeRecord`` candidates (status=CANDIDATE,
verification=UNVERIFIED) carrying full provenance. Validation/acceptance (§4)
and conflict detection (§5) run on these candidates in later increments — the
extractor never accepts or persists anything itself.

The extractor is deterministic and rule-based (no model call), mirroring the
pure, network-free style of ``brain.reasoner.ReasoningEngine``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core.types import Timestamp
from retention.record import (
    Durability,
    KnowledgeKind,
    KnowledgeRecord,
    KnowledgeStatus,
    VerificationStatus,
)

# ---------------------------------------------------------------------------
# Do-not-store detection
# ---------------------------------------------------------------------------

# Secret-like patterns — a segment matching any of these is dropped, never
# stored. Kept deliberately broad; false positives cost us a candidate, false
# negatives could persist a credential.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"),                       # OpenAI-style
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}\b"),                # Anthropic-style
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),                      # GitHub token
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"),            # Slack token
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                          # AWS access key
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}\b", re.I),      # Bearer token
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),            # PEM private key
    re.compile(r"(?i)\b(?:api[_-]?key|secret|password|passwd|token)\b\s*[:=]\s*\S{6,}"),
)

# Chain-of-thought regions to strip before segmentation. Tag blocks first,
# then reasoning-preamble lines. We preserve the final answer + rationale, not
# hidden reasoning (§2).
_COT_BLOCK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"<thinking>.*?</thinking>", re.I | re.S),
    re.compile(r"<reasoning>.*?</reasoning>", re.I | re.S),
    re.compile(r"<scratchpad>.*?</scratchpad>", re.I | re.S),
)
_COT_LINE_PATTERN = re.compile(
    r"^\s*(?:thinking|reasoning|scratchpad|chain[- ]of[- ]thought|let me think)\b.*$",
    re.I,
)

# Politeness / filler phrases. A short sentence dominated by these is casual
# conversation, not knowledge.
_CASUAL_PHRASES: tuple[str, ...] = (
    "happy to help", "hope this helps", "let me know", "feel free",
    "great question", "good question", "no problem", "you're welcome",
    "as an ai", "here's a quick", "here is a quick", "in summary",
    "to summarize", "as mentioned", "thanks for", "sure thing",
    "let's dive", "i'd be happy", "of course",
)
_CASUAL_LEADING = re.compile(r"^\s*(sure|certainly|absolutely|great|thanks|okay|ok)\b[,! ]", re.I)
# Short "Here is my recommendation." style announcements — filler only when
# brief; a longer "Here is the root cause: …" carries knowledge and is kept.
_CASUAL_ANNOUNCE = re.compile(r"^\s*(here('?s| is| are)|below is|the following)\b", re.I)

# Transient status markers → not durable knowledge (§2).
_STATUS_MARKERS: tuple[str, ...] = (
    "currently", "at the moment", "for now", "as of now", "as of today",
    "right now", "in progress", "still running", "at this time",
    "for the time being", "temporarily",
)

# Hedging without evidence → unsupported speculation (§2). Not applied to
# questions, which we deliberately keep.
_SPECULATION_MARKERS: tuple[str, ...] = (
    "maybe", "perhaps", "probably", "possibly", "i think", "i believe",
    "i guess", "not sure", "might be", "could be", "seems like",
    "my guess", "presumably",
)

# ---------------------------------------------------------------------------
# Kind classification signals (checked in priority order)
# ---------------------------------------------------------------------------

_QUESTION_MARKERS = ("unresolved", "open question", "unclear", "tbd",
                     "still need to", "to be determined", "we should decide")
_DECISION_MARKERS = ("decided", "decision", "chose", "chosen", "choose", "adopt",
                     "going with", "will use", "should use", "opt for",
                     "settled on", "we'll use", "we will use")
_REQUIREMENT_MARKERS = ("must ", "shall ", "required", "mandatory", "has to ",
                        "have to ", "needs to ", "is required", "requirement")
_RISK_MARKERS = ("risk", "vulnerab", "danger", "insecure", "can fail",
                 "could break", "may break", "warning", "caution",
                 "be careful", "leak", "exposure", "unsafe")
_LESSON_MARKERS = ("learned", "turned out", "in hindsight", "next time",
                   "gotcha", "mistake", "root cause", "we found that",
                   "the fix was", "lesson")
_PATTERN_MARKERS = ("pattern", "convention", "always ", "prefer ", "whenever ",
                    "as a rule", "best practice", "idiom", "by convention")

# Volatile subjects that should age quickly regardless of kind (§9).
_VOLATILE_MARKERS = ("price", "pricing", "cost per", "per token", "version",
                     "latest", "release", "as of")

_NUMBERED_STEP = re.compile(r"^\s*\d+[.)]\s+\S")
_BULLET = re.compile(r"^\s*[-*]\s+\S")
_HEADING = re.compile(r"^\s*#{1,6}\s+")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")


@dataclass
class DroppedSegment:
    """A segment the extractor chose not to store, with the reason why (§8)."""

    text: str
    reason: str  # secret | chain_of_thought | casual | status_update |
    #              speculation | duplicate | too_short


@dataclass
class ExtractionResult:
    """Candidates extracted from one response, plus an audit of what was dropped."""

    candidates: list[KnowledgeRecord] = field(default_factory=list)
    dropped: list[DroppedSegment] = field(default_factory=list)

    @property
    def kept(self) -> int:
        return len(self.candidates)

    def dropped_for(self, reason: str) -> list[DroppedSegment]:
        return [d for d in self.dropped if d.reason == reason]


class KnowledgeExtractor:
    """Turns provider response text into structured knowledge candidates (§2)."""

    def __init__(self, project: str = "mondayos", min_words: int = 3) -> None:
        self._project = project
        self._min_words = min_words

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(
        self,
        text: str,
        *,
        provider: str = "",
        model: str = "",
        run: str = "",
        task: str = "",
        project: str | None = None,
        now: Timestamp | None = None,
    ) -> ExtractionResult:
        """Extract knowledge candidates from a provider response."""
        now = now or datetime.now(tz=timezone.utc)
        project = project or self._project
        result = ExtractionResult()

        cleaned = self._strip_chain_of_thought(text, result)
        seen: set[str] = set()

        for segment, forced_kind in self._segment(cleaned):
            self._process_segment(
                segment, forced_kind, seen, result,
                provider=provider, model=model, run=run, task=task,
                project=project, now=now,
            )
        return result

    def extract_from_response(
        self,
        response: Any,
        *,
        run: str = "",
        task: str = "",
        project: str | None = None,
        now: Timestamp | None = None,
    ) -> ExtractionResult:
        """Extract from a ``ProviderResponse`` (duck-typed: content/model/provider)."""
        return self.extract(
            getattr(response, "content", "") or "",
            provider=getattr(response, "provider", ""),
            model=getattr(response, "model", ""),
            run=run, task=task, project=project, now=now,
        )

    # ------------------------------------------------------------------
    # Segmentation
    # ------------------------------------------------------------------

    def _strip_chain_of_thought(self, text: str, result: ExtractionResult) -> str:
        """Remove hidden-reasoning blocks/lines; record that they were dropped."""
        cleaned = text
        for pattern in _COT_BLOCK_PATTERNS:
            for match in pattern.findall(cleaned):
                snippet = match if isinstance(match, str) else " ".join(match)
                result.dropped.append(
                    DroppedSegment(text=snippet[:120], reason="chain_of_thought")
                )
            cleaned = pattern.sub(" ", cleaned)

        kept_lines: list[str] = []
        for line in cleaned.splitlines():
            if _COT_LINE_PATTERN.match(line):
                result.dropped.append(DroppedSegment(text=line.strip()[:120], reason="chain_of_thought"))
                continue
            kept_lines.append(line)
        return "\n".join(kept_lines)

    def _segment(self, text: str) -> list[tuple[str, KnowledgeKind | None]]:
        """Split text into candidate segments.

        Returns (segment_text, forced_kind). A numbered step list becomes one
        RUNBOOK segment; bullets and prose sentences are individual segments
        with no forced kind (classified later).
        """
        segments: list[tuple[str, KnowledgeKind | None]] = []
        blocks = re.split(r"\n\s*\n", text)
        for block in blocks:
            lines = [ln for ln in block.splitlines() if ln.strip()]
            if not lines:
                continue

            numbered = [ln for ln in lines if _NUMBERED_STEP.match(ln)]
            if len(numbered) >= 2:
                # A multi-step numbered procedure — keep as one runbook.
                segments.append((block.strip(), KnowledgeKind.RUNBOOK))
                continue

            for line in lines:
                stripped = _HEADING.sub("", line).strip()
                if _BULLET.match(line):
                    stripped = _BULLET.sub("", line).strip()
                    segments.append((stripped, None))
                elif _NUMBERED_STEP.match(line):
                    segments.append((re.sub(r"^\s*\d+[.)]\s+", "", line).strip(), None))
                else:
                    for sentence in _SENTENCE_SPLIT.split(stripped):
                        s = sentence.strip()
                        if s:
                            segments.append((s, None))
        return segments

    # ------------------------------------------------------------------
    # Per-segment processing
    # ------------------------------------------------------------------

    def _process_segment(
        self,
        segment: str,
        forced_kind: KnowledgeKind | None,
        seen: set[str],
        result: ExtractionResult,
        *,
        provider: str,
        model: str,
        run: str,
        task: str,
        project: str,
        now: Timestamp,
    ) -> None:
        text = segment.strip().strip("-*• ").strip()
        if not text:
            return

        lower = text.lower()
        is_question = text.endswith("?") or any(m in lower for m in _QUESTION_MARKERS)

        # 1. Secrets — never stored, checked first.
        if self._contains_secret(text):
            result.dropped.append(DroppedSegment(text="<redacted>", reason="secret"))
            return

        # 2. Too short to be knowledge (questions get a slightly lower bar).
        word_count = len(text.split())
        floor = 3 if is_question else self._min_words
        if word_count < floor:
            result.dropped.append(DroppedSegment(text=text, reason="too_short"))
            return

        # 3. Casual conversation / filler.
        if self._is_casual(text, lower, word_count):
            result.dropped.append(DroppedSegment(text=text, reason="casual"))
            return

        # 4. Transient status updates — not durable knowledge.
        if any(m in lower for m in _STATUS_MARKERS):
            result.dropped.append(DroppedSegment(text=text, reason="status_update"))
            return

        kind = forced_kind or self._classify_kind(lower, is_question)

        # 5. Unsupported speculation (questions are exempt — a gap is knowledge).
        if kind != KnowledgeKind.QUESTION and self._is_speculation(lower):
            result.dropped.append(DroppedSegment(text=text, reason="speculation"))
            return

        # 6. Repetitive prose — exact-normalized duplicate of an earlier keeper.
        norm = re.sub(r"[^a-z0-9 ]", "", lower).strip()
        norm = re.sub(r"\s+", " ", norm)
        if norm in seen:
            result.dropped.append(DroppedSegment(text=text, reason="duplicate"))
            return
        seen.add(norm)

        result.candidates.append(
            self._build_candidate(
                text, kind, provider=provider, model=model, run=run,
                task=task, project=project, now=now,
            )
        )

    def _contains_secret(self, text: str) -> bool:
        return any(p.search(text) for p in _SECRET_PATTERNS)

    def _is_casual(self, text: str, lower: str, word_count: int) -> bool:
        if _CASUAL_LEADING.match(text) and word_count < 12:
            return True
        if _CASUAL_ANNOUNCE.match(text) and word_count < 7:
            return True
        # A short sentence that contains a politeness phrase is filler.
        return word_count < 14 and any(p in lower for p in _CASUAL_PHRASES)

    def _is_speculation(self, lower: str) -> bool:
        return any(m in lower for m in _SPECULATION_MARKERS)

    def _classify_kind(self, lower: str, is_question: bool) -> KnowledgeKind:
        if is_question:
            return KnowledgeKind.QUESTION
        if any(m in lower for m in _DECISION_MARKERS):
            return KnowledgeKind.DECISION
        if any(m in lower for m in _REQUIREMENT_MARKERS):
            return KnowledgeKind.REQUIREMENT
        if any(m in lower for m in _RISK_MARKERS):
            return KnowledgeKind.RISK
        if any(m in lower for m in _LESSON_MARKERS):
            return KnowledgeKind.LESSON
        if any(m in lower for m in _PATTERN_MARKERS):
            return KnowledgeKind.PATTERN
        return KnowledgeKind.CLAIM

    def _build_candidate(
        self,
        text: str,
        kind: KnowledgeKind,
        *,
        provider: str,
        model: str,
        run: str,
        task: str,
        project: str,
        now: Timestamp,
    ) -> KnowledgeRecord:
        durability = self._durability_for(kind, text.lower())
        title = self._title_for(text)
        # Decisions/requirements read as firmer assertions than a bare claim.
        confidence = 0.6 if kind in (KnowledgeKind.DECISION, KnowledgeKind.REQUIREMENT) else 0.5
        return KnowledgeRecord(
            knowledge_id="",  # assigned by the persistence layer (later increment)
            kind=kind,
            title=title,
            canonical_statement=text,
            summary=f"Extracted from {provider or 'model'} response"
            + (f" ({model})" if model else ""),
            project=project,
            tags=[],
            source_provider=provider,
            source_model=model,
            source_run=run,
            source_task=task,
            confidence=confidence,
            verification=VerificationStatus.UNVERIFIED,
            status=KnowledgeStatus.CANDIDATE,
            durability=durability,
            created_at=now,
            metadata={"extracted": True},
        )

    def _durability_for(self, kind: KnowledgeKind, lower: str) -> Durability:
        if any(m in lower for m in _VOLATILE_MARKERS):
            return Durability.TIME_SENSITIVE
        if kind == KnowledgeKind.RUNBOOK:
            return Durability.VERSION_BOUND
        return Durability.DURABLE

    def _title_for(self, text: str) -> str:
        """A short title: the first clause, truncated on a word boundary."""
        head = re.split(r"[.:;\n]", text, maxsplit=1)[0].strip()
        if len(head) <= 80:
            return head
        return head[:77].rsplit(" ", 1)[0] + "…"
