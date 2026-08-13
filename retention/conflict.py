"""Conflict detection and merge/supersede proposals (§5).

Before a validated candidate is persisted, it is checked against existing
project and organizational knowledge to find duplicates, near-duplicates,
contradictions, and superseding relationships. The detector **never applies a
change** — it returns proposals for a later persistence step (§6) to act on, so
accepted knowledge is never silently overwritten.

Matching is deterministic and requires no provider call (§4). A
``SimilarityMatcher`` protocol leaves a seam for a future local embedding /
semantic matcher; ``DeterministicMatcher`` is the default lexical implementation.
Deterministic rules can catch obvious structured contradictions but not every
semantic one — anything ambiguous is routed to CONFLICT_REVIEW (§5).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol

from core.types import EntityId, Timestamp
from retention.record import (
    Citation,
    KnowledgeKind,
    KnowledgeRecord,
    KnowledgeStatus,
    VerificationStatus,
)
from retention.validation import _FINANCIAL_RE, _LEGAL_RE, _SECURITY_RE, ReviewPriority

# ---------------------------------------------------------------------------
# Normalisation & feature extraction
# ---------------------------------------------------------------------------

_ARTICLES = frozenset({"the", "a", "an"})
_NEGATIONS = frozenset(
    {"not", "never", "no", "without", "cannot", "disable", "disabled", "off", "none"}
)
# Antonym pairs — one side in the candidate and the other in the record, over a
# shared subject, is an obvious contradiction. (must/must-not is handled by the
# negation-asymmetry rule, not here.)
_ANTONYM_PAIRS: tuple[tuple[frozenset[str], frozenset[str]], ...] = (
    (frozenset({"enabled", "enable", "on", "true"}), frozenset({"disabled", "disable", "off", "false"})),
    (frozenset({"local", "localhost"}), frozenset({"remote", "external", "public"})),
    (frozenset({"allow", "allowed", "accept", "accepted"}), frozenset({"deny", "denied", "reject", "rejected", "block", "blocked"})),
    (frozenset({"public"}), frozenset({"private"})),
    (frozenset({"sync", "synchronous"}), frozenset({"async", "asynchronous"})),
    (frozenset({"required", "mandatory"}), frozenset({"optional", "forbidden", "prohibited"})),
)

_REPLACE_RE = re.compile(
    r"\b(replaces?|supersedes?|instead of|no longer|now use[sd]?|deprecat\w*|"
    r"updated to|migrate[sd]? to|switch(?:ed)? to|as of now use)\b", re.I)

_ENTITY_PATTERNS = (
    re.compile(r"\b[A-Z][A-Za-z0-9]{2,}\b"),      # Proper/CamelCase (Postgres, API)
    re.compile(r"\bv\d+(?:\.\d+)*\b", re.I),       # v1, v2.3
    re.compile(r"\b\d+(?:\.\d+)+\b"),              # 3.11, 1.0.0
    re.compile(r"\b\d{2,}\b"),                     # multi-digit numbers
)
_BACKTICK = re.compile(r"`([^`]+)`")
_VALUE_LIKE = re.compile(r"^v?\d")
# Common words that get capitalized at sentence start — not real entities.
# Without this filter, two sentences both starting "The …" would share a
# spurious entity and read as the same subject.
_ENTITY_STOP = frozenset(
    "the a an this that these those there then they them when where which what "
    "who why how here and or but for with from into onto our your its it is are "
    "was were be by of to in on we you if as at not no".split()
)


def _normalize(text: str) -> str:
    text = re.sub(r"[^a-z0-9 ]", " ", text.lower())
    return " ".join(t for t in text.split() if t and t not in _ARTICLES)


def _tokens(text: str) -> set[str]:
    return set(_normalize(text).split())


def _entities(text: str) -> set[str]:
    ents: set[str] = set()
    for pat in _ENTITY_PATTERNS:
        for m in pat.findall(text):
            low = m.lower()
            if low not in _ENTITY_STOP:
                ents.add(low)
    for m in _BACKTICK.findall(text):
        ents.add(m.strip().lower())
    return ents


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ---------------------------------------------------------------------------
# Similarity matcher (§4)
# ---------------------------------------------------------------------------


@dataclass
class MatchScore:
    """Per-axis similarity between a candidate and an existing record."""

    token_overlap: float
    title_similarity: float
    tag_overlap: float
    entity_overlap: float
    length_ratio: float
    kind_match: float
    composite: float

    def same_subject(self) -> bool:
        """A shared subject — the precondition for contradiction checks."""
        return self.entity_overlap >= 0.3 or self.token_overlap >= 0.4


class SimilarityMatcher(Protocol):
    """Interface for candidate↔record similarity. A future local embedding
    matcher can implement this without touching the detector."""

    def similarity(self, candidate: KnowledgeRecord, record: KnowledgeRecord) -> MatchScore:
        ...


class DeterministicMatcher:
    """Lexical, deterministic matcher — no provider call, no network."""

    _W_TOKEN = 0.40
    _W_TITLE = 0.15
    _W_TAG = 0.10
    _W_KIND = 0.10
    _W_LEN = 0.05
    _W_ENTITY = 0.20

    def similarity(self, candidate: KnowledgeRecord, record: KnowledgeRecord) -> MatchScore:
        ctoks, rtoks = _tokens(candidate.canonical_statement), _tokens(record.canonical_statement)
        token_overlap = _jaccard(ctoks, rtoks)
        title_sim = _jaccard(_tokens(candidate.title), _tokens(record.title))
        tag_overlap = _jaccard({t.lower() for t in candidate.tags}, {t.lower() for t in record.tags})
        entity_overlap = _jaccard(_entities(candidate.canonical_statement), _entities(record.canonical_statement))
        cl, rl = len(ctoks), len(rtoks)
        length_ratio = (min(cl, rl) / max(cl, rl)) if max(cl, rl) else 0.0
        kind_match = 1.0 if candidate.kind == record.kind else 0.0
        composite = (
            self._W_TOKEN * token_overlap
            + self._W_TITLE * title_sim
            + self._W_TAG * tag_overlap
            + self._W_KIND * kind_match
            + self._W_LEN * length_ratio
            + self._W_ENTITY * entity_overlap
        )
        return MatchScore(token_overlap, title_sim, tag_overlap, entity_overlap,
                          length_ratio, kind_match, min(1.0, composite))


# ---------------------------------------------------------------------------
# Outcomes, proposals, result (§2, §7, §8)
# ---------------------------------------------------------------------------


class ConflictOutcome(Enum):
    NEW = "new"
    EXACT_DUPLICATE = "exact_duplicate"
    NEAR_DUPLICATE = "near_duplicate"
    MERGE_PROPOSED = "merge_proposed"
    SUPERSEDE_PROPOSED = "supersede_proposed"
    CONFLICT_REVIEW = "conflict_review"
    NO_ACTION = "no_action"


_VERIF_RANK: dict[VerificationStatus, int] = {
    VerificationStatus.CONTRADICTED: 0,
    VerificationStatus.UNVERIFIED: 1,
    VerificationStatus.TEST_BACKED: 2,
    VerificationStatus.WORKFLOW_APPROVED: 3,
    VerificationStatus.HUMAN_APPROVED: 4,
}


@dataclass
class MergeProposal:
    """A proposed (not applied) merge of a candidate into a target record (§7)."""

    target_record_id: EntityId
    candidate_record_id: EntityId
    combined_canonical_statement: str
    combined_provenance: list[dict[str, str]]
    strongest_confidence: float
    strongest_verification: VerificationStatus
    preserved_evidence: list[Citation]
    fields_changed: list[str]
    rationale: str


@dataclass
class SupersedeProposal:
    """A proposed (not applied) supersession of a target by a candidate (§6)."""

    target_record_id: EntityId
    candidate_record_id: EntityId
    rationale: str
    review_required: bool
    signals: list[str] = field(default_factory=list)


@dataclass
class ConflictResult:
    """The explained result of checking one candidate against existing knowledge (§8)."""

    outcome: ConflictOutcome
    recommended_action: str
    explanation: str
    matched_record_ids: list[EntityId] = field(default_factory=list)
    similarity_scores: dict[EntityId, float] = field(default_factory=dict)
    contradiction_signals: list[str] = field(default_factory=list)
    merge_proposal: MergeProposal | None = None
    supersede_proposal: SupersedeProposal | None = None
    review_priority: ReviewPriority = ReviewPriority.NONE

    def to_dict(self) -> dict[str, object]:
        return {
            "outcome": self.outcome.value,
            "recommended_action": self.recommended_action,
            "explanation": self.explanation,
            "matched_record_ids": list(self.matched_record_ids),
            "similarity_scores": {k: round(v, 3) for k, v in self.similarity_scores.items()},
            "contradiction_signals": list(self.contradiction_signals),
            "review_priority": self.review_priority.value,
            "has_merge_proposal": self.merge_proposal is not None,
            "has_supersede_proposal": self.supersede_proposal is not None,
        }


@dataclass
class _Contradiction:
    signals: list[str]
    obvious: bool


# ---------------------------------------------------------------------------
# Detector (§5)
# ---------------------------------------------------------------------------


class ConflictDetector:
    """Detects duplicates, contradictions, and supersession before persistence."""

    # Similarity bands (composite score).
    STRONG = 0.60   # near-duplicate → merge
    WEAK = 0.40     # similar → flag, keep separate

    # Kinds/verification that must never be auto-superseded (§6).
    _PROTECTED_KINDS = frozenset({KnowledgeKind.DECISION})

    def __init__(self, matcher: SimilarityMatcher | None = None) -> None:
        self._matcher = matcher or DeterministicMatcher()

    def detect(
        self,
        candidate: KnowledgeRecord,
        existing: list[KnowledgeRecord],
        now: Timestamp | None = None,
    ) -> ConflictResult:
        now = now or datetime.now(tz=timezone.utc)
        comparable = [r for r in existing if self._comparable(candidate, r)]
        scores: dict[EntityId, float] = {}

        obvious: list[tuple[KnowledgeRecord, list[str]]] = []
        exacts: list[KnowledgeRecord] = []
        supersedes: list[tuple[KnowledgeRecord, list[str]]] = []
        ambiguous: list[tuple[KnowledgeRecord, list[str]]] = []
        strong: list[tuple[KnowledgeRecord, MatchScore]] = []
        weak: list[tuple[KnowledgeRecord, MatchScore]] = []

        for record in comparable:
            ms = self._matcher.similarity(candidate, record)
            scores[record.knowledge_id] = ms.composite
            contra = self._contradiction(candidate, record, ms)
            if contra and contra.obvious:
                obvious.append((record, contra.signals))
                continue
            if self._is_exact(candidate, record):
                exacts.append(record)
                continue
            sup = self._supersession(candidate, record, ms, now)
            if sup:
                supersedes.append((record, sup))
                continue
            if contra and not contra.obvious:
                ambiguous.append((record, contra.signals))
                continue
            if ms.composite >= self.STRONG:
                strong.append((record, ms))
            elif ms.composite >= self.WEAK:
                weak.append((record, ms))

        # Precedence: obvious conflict > exact dup > supersession > ambiguous
        # conflict > merge > near-dup > new.
        if obvious:
            signals = [s for _, sigs in obvious for s in sigs]
            return ConflictResult(
                outcome=ConflictOutcome.CONFLICT_REVIEW,
                recommended_action="route to human review; do not overwrite accepted knowledge",
                explanation="Obvious contradiction with existing knowledge: " + "; ".join(signals),
                matched_record_ids=[r.knowledge_id for r, _ in obvious],
                similarity_scores=scores, contradiction_signals=signals,
                review_priority=ReviewPriority.HIGH,
            )

        if exacts:
            target = exacts[0]
            proposal = self._merge_proposal(candidate, target, exact=True)
            return ConflictResult(
                outcome=ConflictOutcome.EXACT_DUPLICATE,
                recommended_action="do not create a new record; fold candidate provenance into the "
                "target and retain the strongest confidence/verification",
                explanation="Candidate is an exact duplicate (after normalisation) of "
                f"{target.knowledge_id}; no new record needed.",
                matched_record_ids=[target.knowledge_id], similarity_scores=scores,
                merge_proposal=proposal, review_priority=ReviewPriority.NONE,
            )

        if supersedes:
            target, signals = supersedes[0]
            protected = self._is_protected(target)
            proposal = SupersedeProposal(
                target_record_id=target.knowledge_id, candidate_record_id=candidate.knowledge_id,
                rationale="; ".join(signals), review_required=protected, signals=signals,
            )
            if protected:
                return ConflictResult(
                    outcome=ConflictOutcome.CONFLICT_REVIEW,
                    recommended_action="requires human review; protected knowledge is never "
                    "auto-superseded",
                    explanation=f"Candidate would supersede protected record {target.knowledge_id} "
                    f"({target.kind.value}); {'; '.join(signals)}. Held for review.",
                    matched_record_ids=[target.knowledge_id], similarity_scores=scores,
                    supersede_proposal=proposal, review_priority=ReviewPriority.HIGH,
                )
            return ConflictResult(
                outcome=ConflictOutcome.SUPERSEDE_PROPOSED,
                recommended_action="propose superseding the target; await approval before applying",
                explanation=f"Candidate proposes replacing {target.knowledge_id}: {'; '.join(signals)}.",
                matched_record_ids=[target.knowledge_id], similarity_scores=scores,
                supersede_proposal=proposal, review_priority=ReviewPriority.MEDIUM,
            )

        if ambiguous:
            signals = [s for _, sigs in ambiguous for s in sigs]
            return ConflictResult(
                outcome=ConflictOutcome.CONFLICT_REVIEW,
                recommended_action="route to human review; contradiction could not be resolved "
                "deterministically",
                explanation="Ambiguous contradiction with existing knowledge: " + "; ".join(signals),
                matched_record_ids=[r.knowledge_id for r, _ in ambiguous],
                similarity_scores=scores, contradiction_signals=signals,
                review_priority=ReviewPriority.MEDIUM,
            )

        if strong:
            target, _ = max(strong, key=lambda t: t[1].composite)
            proposal = self._merge_proposal(candidate, target, exact=False)
            return ConflictResult(
                outcome=ConflictOutcome.MERGE_PROPOSED,
                recommended_action="propose merge into the target; await approval before applying",
                explanation=f"Candidate is a near-duplicate of {target.knowledge_id} "
                f"(similarity {scores[target.knowledge_id]:.2f}); merge proposed.",
                matched_record_ids=[target.knowledge_id], similarity_scores=scores,
                merge_proposal=proposal, review_priority=ReviewPriority.LOW,
            )

        if weak:
            target, _ = max(weak, key=lambda t: t[1].composite)
            return ConflictResult(
                outcome=ConflictOutcome.NEAR_DUPLICATE,
                recommended_action="a similar record exists; keep separate unless a reviewer merges",
                explanation=f"Candidate resembles {target.knowledge_id} "
                f"(similarity {scores[target.knowledge_id]:.2f}) but is below the merge threshold.",
                matched_record_ids=[target.knowledge_id], similarity_scores=scores,
                review_priority=ReviewPriority.NONE,
            )

        return ConflictResult(
            outcome=ConflictOutcome.NEW,
            recommended_action="create a new record; no matching knowledge found",
            explanation="No duplicate, contradiction, or supersession found.",
            similarity_scores=scores, review_priority=ReviewPriority.NONE,
        )

    def detect_safe(
        self,
        candidate: KnowledgeRecord,
        existing: list[KnowledgeRecord],
        now: Timestamp | None = None,
    ) -> ConflictResult:
        """Detect without ever raising (§10). On error, route to review so
        nothing is auto-merged or auto-superseded — a detector bug must not
        break the provider-response path, nor silently overwrite knowledge."""
        try:
            return self.detect(candidate, existing, now=now)
        except Exception as exc:  # noqa: BLE001 — deliberately defensive
            return ConflictResult(
                outcome=ConflictOutcome.CONFLICT_REVIEW,
                recommended_action="route to human review; conflict detection failed",
                explanation=f"conflict-detection error, held for review: {type(exc).__name__}: {exc}",
                review_priority=ReviewPriority.HIGH,
            )

    # ------------------------------------------------------------------
    # Comparability & project isolation (§9)
    # ------------------------------------------------------------------

    def _comparable(self, candidate: KnowledgeRecord, record: KnowledgeRecord) -> bool:
        if not record.canonical_statement.strip():
            return False
        if record.knowledge_id and record.knowledge_id == candidate.knowledge_id:
            return False  # never compare against itself
        if not record.is_usable():  # exclude rejected/superseded
            return False
        if candidate.project == record.project:
            return True
        # Cross-project matches only when one side is organizational knowledge.
        return self._is_org(record) or self._is_org(candidate)

    @staticmethod
    def _is_org(record: KnowledgeRecord) -> bool:
        return record.metadata.get("origin") == "mks" or bool(record.metadata.get("organizational"))

    def _is_protected(self, record: KnowledgeRecord) -> bool:
        if record.kind in self._PROTECTED_KINDS:
            return True
        if record.verification == VerificationStatus.HUMAN_APPROVED:
            return True
        text = f"{record.title} {record.canonical_statement} {record.summary}".lower()
        return bool(_SECURITY_RE.search(text) or _FINANCIAL_RE.search(text) or _LEGAL_RE.search(text))

    # ------------------------------------------------------------------
    # Duplicate / contradiction / supersession logic
    # ------------------------------------------------------------------

    def _is_exact(self, candidate: KnowledgeRecord, record: KnowledgeRecord) -> bool:
        return _normalize(candidate.canonical_statement) == _normalize(record.canonical_statement)

    def _contradiction(
        self, candidate: KnowledgeRecord, record: KnowledgeRecord, ms: MatchScore
    ) -> _Contradiction | None:
        declared = bool(record.knowledge_id) and record.knowledge_id in candidate.conflicts_with
        subject = ms.same_subject()
        if not (subject or declared):
            return None

        ctoks, rtoks = _tokens(candidate.canonical_statement), _tokens(record.canonical_statement)
        signals: list[str] = []
        obvious = declared
        if declared:
            signals.append(f"declared conflict with {record.knowledge_id}")

        if subject:
            for a, b in _ANTONYM_PAIRS:
                if (ctoks & a and rtoks & b) or (ctoks & b and rtoks & a):
                    obvious = True
                    signals.append(f"antonym: {sorted((ctoks | rtoks) & (a | b))}")
            # Negation asymmetry: exactly one side is negated AND the two
            # statements are otherwise near-identical. Requiring the
            # non-negation tokens to match closely avoids misreading an added
            # qualifying clause ("...when no host is configured") as a flip.
            neg_c, neg_r = bool(ctoks & _NEGATIONS), bool(rtoks & _NEGATIONS)
            if neg_c != neg_r and _jaccard(ctoks - _NEGATIONS, rtoks - _NEGATIONS) >= 0.8:
                obvious = True
                signals.append("negation asymmetry over an otherwise-identical statement")

            if not obvious:
                vm = self._value_mismatch(candidate, record, ms)
                if vm:
                    signals.append(vm)

        if not signals:
            return None
        return _Contradiction(signals=signals, obvious=obvious)

    def _value_mismatch(
        self, candidate: KnowledgeRecord, record: KnowledgeRecord, ms: MatchScore
    ) -> str | None:
        cv = {e for e in _entities(candidate.canonical_statement) if _VALUE_LIKE.match(e)}
        rv = {e for e in _entities(record.canonical_statement) if _VALUE_LIKE.match(e)}
        if cv and rv and cv != rv and ms.token_overlap >= 0.5:
            return f"value mismatch: {sorted(cv)} vs {sorted(rv)}"
        return None

    def _supersession(
        self, candidate: KnowledgeRecord, record: KnowledgeRecord, ms: MatchScore, now: Timestamp
    ) -> list[str] | None:
        # Only established (accepted) knowledge is superseded; do not overwrite
        # other candidates or reviewed-but-unaccepted records.
        if record.status != KnowledgeStatus.ACCEPTED:
            return None

        declared = bool(record.knowledge_id) and candidate.supersedes == record.knowledge_id
        if not (declared or ms.same_subject()):
            return None

        signals: list[str] = []
        if declared:
            signals.append(f"explicitly marked to supersede {record.knowledge_id}")
        if _REPLACE_RE.search(candidate.canonical_statement):
            signals.append("explicit replacement language")
        if record.is_stale(now):
            signals.append("prior record is stale")
        if _VERIF_RANK[candidate.verification] > _VERIF_RANK[record.verification]:
            signals.append("candidate has stronger provenance")
        return signals or None

    # ------------------------------------------------------------------
    # Merge proposal (§7)
    # ------------------------------------------------------------------

    def _merge_proposal(
        self, candidate: KnowledgeRecord, target: KnowledgeRecord, *, exact: bool
    ) -> MergeProposal:
        strongest_conf = max(candidate.confidence, target.confidence)
        strongest_verif = max(
            (candidate.verification, target.verification), key=lambda v: _VERIF_RANK[v]
        )
        # Prefer the more informative statement as canonical.
        combined_statement = (
            target.canonical_statement
            if len(target.canonical_statement) >= len(candidate.canonical_statement)
            else candidate.canonical_statement
        )
        provenance = _dedupe_provenance([_provenance(target), _provenance(candidate)])
        evidence = _dedupe_citations(target.citations + candidate.citations)

        fields_changed: list[str] = []
        if strongest_conf > target.confidence:
            fields_changed.append("confidence")
        if strongest_verif != target.verification:
            fields_changed.append("verification")
        if _provenance(candidate) not in [_provenance(target)]:
            fields_changed.append("provenance")
        if {t.lower() for t in candidate.tags} - {t.lower() for t in target.tags}:
            fields_changed.append("tags")
        if not exact and combined_statement != target.canonical_statement:
            fields_changed.append("canonical_statement")

        rationale = (
            "Exact duplicate: fold provenance/evidence into the target and keep the "
            "strongest confidence and verification."
            if exact
            else "Near-duplicate: combine provenance/evidence and keep the strongest "
            "confidence and verification; canonical statement is the more informative one."
        )
        return MergeProposal(
            target_record_id=target.knowledge_id,
            candidate_record_id=candidate.knowledge_id,
            combined_canonical_statement=combined_statement,
            combined_provenance=provenance,
            strongest_confidence=strongest_conf,
            strongest_verification=strongest_verif,
            preserved_evidence=evidence,
            fields_changed=fields_changed,
            rationale=rationale,
        )


def _provenance(record: KnowledgeRecord) -> dict[str, str]:
    return {
        "provider": record.source_provider,
        "model": record.source_model,
        "run": record.source_run,
        "task": record.source_task,
    }


def _dedupe_provenance(items: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, ...]] = set()
    out: list[dict[str, str]] = []
    for it in items:
        key = tuple(sorted(it.items()))
        if key not in seen and any(it.values()):
            seen.add(key)
            out.append(it)
    return out


def _dedupe_citations(citations: list[Citation]) -> list[Citation]:
    seen: set[tuple[str, str, str]] = set()
    out: list[Citation] = []
    for c in citations:
        key = (c.source, c.detail, c.url)
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out
