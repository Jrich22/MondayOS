"""Validation and promotion rules for knowledge candidates (§4).

Core rule: **Monday may only answer from retained knowledge when that knowledge
has passed validation appropriate to its risk and source.** This module decides
what happens to each ``KnowledgeRecord`` candidate produced by extraction (§2):

    AUTO_ACCEPT           — low-risk, evidence-backed → answerable from memory
    REVIEWED              — a workflow/human already vetted a review-required kind
    REQUIRE_HUMAN_REVIEW  — high-risk or unverified → held for a human
    REJECT                — unsafe/unsupported/malformed → never used
    DEFER                 — plausible but needs evidence → held pending evidence

The validator is deterministic and side-effect free; ``ValidationResult.apply``
maps the outcome onto a record's status/verification fields. It never persists —
persistence stays behind the explicit ``KnowledgeStore`` seam (§8). Semantic
conflict resolution (§5), dashboard metrics (§8), and trust learning (§7) are
deliberately out of scope here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from typing import Callable

from core.types import Timestamp
from retention.extraction import (
    ExtractionResult,
    _SECRET_PATTERNS,
    _SPECULATION_MARKERS,
    _STATUS_MARKERS,
)
from retention.record import (
    Citation,
    Durability,
    KnowledgeKind,
    KnowledgeRecord,
    KnowledgeStatus,
    VerificationStatus,
    suggest_review_after,
)

# ---------------------------------------------------------------------------
# Evidence model (§6)
# ---------------------------------------------------------------------------


class EvidenceType(Enum):
    """Kinds of structured support a candidate can carry."""

    TEST_RESULT = "test_result"                  # a test/suite run
    BUILD_RESULT = "build_result"                # a production build
    DETERMINISTIC_CHECK = "deterministic_check"  # a reproducible check
    APPROVED_FILE = "approved_file"              # derived from an approved file
    WORKFLOW_APPROVAL = "workflow_approval"      # cleared a team workflow
    HUMAN_APPROVAL = "human_approval"            # a human approved it
    CITATION = "citation"                        # external source citation
    CONTRADICTION = "contradiction"              # evidence the claim is false


_PASS_RESULTS = frozenset({"pass", "passed", "ok", "success", "green", "true"})
_FAIL_RESULTS = frozenset({"fail", "failed", "contradicted", "false", "red", "error"})


@dataclass
class Evidence:
    """A single piece of structured evidence for or against a candidate (§6)."""

    evidence_type: EvidenceType
    source: str                                   # e.g. "pytest", "cue-app/store.ts"
    result: str = ""                              # "pass" | "fail" | free text
    timestamp: Timestamp | None = None
    checksum: str = ""                            # checksum/version where relevant
    version: str = ""
    test_count: int = 0
    approval_run: str = ""
    citations: list[Citation] = field(default_factory=list)

    def is_passing(self) -> bool:
        return self.result.strip().lower() in _PASS_RESULTS

    def is_contradiction(self) -> bool:
        if self.evidence_type == EvidenceType.CONTRADICTION:
            return True
        return self.result.strip().lower() in _FAIL_RESULTS

    def is_test_backed(self) -> bool:
        """A passing test/build/deterministic check that actually ran."""
        if self.evidence_type not in (
            EvidenceType.TEST_RESULT,
            EvidenceType.BUILD_RESULT,
            EvidenceType.DETERMINISTIC_CHECK,
        ):
            return False
        if not self.is_passing():
            return False
        # A test-result must report at least one test to count as backing.
        if self.evidence_type == EvidenceType.TEST_RESULT and self.test_count <= 0:
            return False
        return True

    def describe(self) -> str:
        parts = [self.evidence_type.value]
        detail = self.result or self.source
        if detail:
            parts.append(f"{detail}")
        if self.test_count:
            parts.append(f"{self.test_count} tests")
        return "(" + ", ".join(parts) + ")"


# ---------------------------------------------------------------------------
# Outcomes & result (§2, §7)
# ---------------------------------------------------------------------------


class PromotionOutcome(Enum):
    """The five promotion outcomes (§2)."""

    AUTO_ACCEPT = "auto_accept"
    REVIEWED = "reviewed"
    REQUIRE_HUMAN_REVIEW = "require_human_review"
    REJECT = "reject"
    DEFER = "defer"


class ReviewPriority(Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class ValidationResult:
    """The explained result of validating one candidate (§7)."""

    outcome: PromotionOutcome
    reasons: list[str]
    resulting_status: KnowledgeStatus
    resulting_verification: VerificationStatus
    confidence_adjustment: float = 0.0
    required_reviewer: str = ""
    review_priority: ReviewPriority = ReviewPriority.NONE
    review_after: Timestamp | None = None
    evidence_summary: str = ""
    decided_at: Timestamp | None = None

    def apply(self, record: KnowledgeRecord, now: Timestamp | None = None) -> KnowledgeRecord:
        """Map the outcome onto the candidate's status/verification fields (§8).

        In-memory only — no persistence. The explanation is preserved on the
        record so a later reviewer/dashboard can see why the outcome was chosen.
        """
        now = now or self.decided_at or datetime.now(tz=timezone.utc)
        record.status = self.resulting_status
        record.verification = self.resulting_verification
        record.confidence = max(0.0, min(1.0, record.confidence + self.confidence_adjustment))
        if self.review_after is not None:
            record.review_after = self.review_after
        if self.resulting_status in (KnowledgeStatus.ACCEPTED, KnowledgeStatus.REVIEWED):
            record.last_verified_at = now
        record.metadata["validation"] = {
            "outcome": self.outcome.value,
            "reasons": list(self.reasons),
            "required_reviewer": self.required_reviewer,
            "review_priority": self.review_priority.value,
            "evidence_summary": self.evidence_summary,
        }
        return record

    def to_dict(self) -> dict[str, object]:
        return {
            "outcome": self.outcome.value,
            "reasons": list(self.reasons),
            "resulting_status": self.resulting_status.value,
            "resulting_verification": self.resulting_verification.value,
            "confidence_adjustment": round(self.confidence_adjustment, 3),
            "required_reviewer": self.required_reviewer,
            "review_priority": self.review_priority.value,
            "review_after": self.review_after.isoformat() if self.review_after else None,
            "evidence_summary": self.evidence_summary,
        }


# ---------------------------------------------------------------------------
# Sensitivity detection
# ---------------------------------------------------------------------------

# Word-boundary regexes, not naive substrings — a substring "nda" would
# otherwise match "sta[nda]rdize" and mislabel a benign decision as legal.
_SECURITY_RE = re.compile(
    r"\bvulnerab|\bexploit|\bcve-\d|\binjection\b|\b(xss|csrf|rce)\b|"
    r"\bauth\w*\s+bypass|\bprivilege escalation|\bunauthenticated\b|"
    r"\binsecure\b|\bsecurity\b|\bcredential|\battack surface", re.I)
_FINANCIAL_RE = re.compile(
    r"\$\s*\d|\b(invoice|revenue|refund|payment|billing|salary|budget|financial)\b|"
    r"\bpric(e|es|ing)\b|\b(cost per|per token|license fee)\b", re.I)
_LEGAL_RE = re.compile(
    r"\b(contract|gdpr|hipaa|liability|lawsuit|copyright|compliance|legal|nda)\b|"
    r"\b(license terms|terms of service)\b", re.I)
_RESEARCH_RE = re.compile(
    r"\b(study|benchmark|paper|white paper)\b|\b(research shows|according to|survey found)\b|"
    r"\bet al\b", re.I)


@dataclass
class Sensitivity:
    security: bool = False
    financial: bool = False
    legal: bool = False
    research: bool = False

    @property
    def any(self) -> bool:
        return self.security or self.financial or self.legal or self.research


# ---------------------------------------------------------------------------
# Validator (§4)
# ---------------------------------------------------------------------------


class KnowledgeValidator:
    """Applies the §4 promotion rules to knowledge candidates."""

    # Kinds that always require review by their nature (architecture decisions).
    _REVIEW_ALWAYS_KINDS = frozenset({KnowledgeKind.DECISION})
    # Recommendation-style kinds that require review when offered without evidence.
    _RECOMMENDATION_KINDS = frozenset({KnowledgeKind.DECISION, KnowledgeKind.PATTERN})

    def __init__(
        self,
        untrusted_sources: frozenset[str] = frozenset(),
        reject_confidence: float = 0.35,
    ) -> None:
        self._untrusted = {s.lower() for s in untrusted_sources}
        self._reject_confidence = reject_confidence

    def validate(
        self,
        candidate: KnowledgeRecord,
        evidence: list[Evidence] | None = None,
        *,
        security: bool | None = None,
        financial: bool | None = None,
        legal: bool | None = None,
        research: bool | None = None,
        now: Timestamp | None = None,
    ) -> ValidationResult:
        now = now or datetime.now(tz=timezone.utc)
        evidence = list(evidence or [])
        text = f"{candidate.title} {candidate.canonical_statement} {candidate.summary}"
        low = text.lower()

        # Evidence rollups
        test_backed = [e for e in evidence if e.is_test_backed()]
        approved_files = [e for e in evidence if e.evidence_type == EvidenceType.APPROVED_FILE]
        workflow_appr = [e for e in evidence if e.evidence_type == EvidenceType.WORKFLOW_APPROVAL]
        human_appr = [e for e in evidence if e.evidence_type == EvidenceType.HUMAN_APPROVAL]
        contradictions = [e for e in evidence if e.is_contradiction()]
        qualifying = bool(test_backed or approved_files or workflow_appr)
        has_approval = bool(workflow_appr or human_appr)
        summary = self._evidence_summary(evidence)

        def result(outcome, reasons, status, verification, *, conf=0.0,
                   reviewer="", priority=ReviewPriority.NONE, review_after=None):
            return ValidationResult(
                outcome=outcome, reasons=reasons, resulting_status=status,
                resulting_verification=verification, confidence_adjustment=conf,
                required_reviewer=reviewer, review_priority=priority,
                review_after=review_after, evidence_summary=summary, decided_at=now,
            )

        # ---------------- REJECT gates (§5) ----------------
        if self._contains_secret(text):
            return result(PromotionOutcome.REJECT, ["secret-bearing content must never be stored"],
                          KnowledgeStatus.REJECTED, candidate.verification, conf=-0.5,
                          priority=ReviewPriority.CRITICAL)

        if not candidate.canonical_statement.strip() or not candidate.title.strip():
            return result(PromotionOutcome.REJECT, ["malformed: missing statement or title"],
                          KnowledgeStatus.REJECTED, candidate.verification)
        if not 0.0 <= candidate.confidence <= 1.0:
            return result(PromotionOutcome.REJECT, ["malformed: confidence out of range"],
                          KnowledgeStatus.REJECTED, candidate.verification)

        if not self._has_provenance(candidate, evidence):
            return result(PromotionOutcome.REJECT, ["lacks provenance: no source, run, task, or citation"],
                          KnowledgeStatus.REJECTED, candidate.verification)

        if any(m in low for m in _STATUS_MARKERS):
            return result(PromotionOutcome.REJECT, ["operationally temporary status, not durable knowledge"],
                          KnowledgeStatus.REJECTED, candidate.verification)

        if contradictions or candidate.verification == VerificationStatus.CONTRADICTED:
            srcs = ", ".join(e.source for e in contradictions) or "prior verification"
            return result(PromotionOutcome.REJECT, [f"evidence contradicts the claim ({srcs})"],
                          KnowledgeStatus.REJECTED, VerificationStatus.CONTRADICTED, conf=-0.4,
                          priority=ReviewPriority.HIGH)

        if candidate.source_provider.lower() in self._untrusted:
            return result(PromotionOutcome.REJECT, [f"source '{candidate.source_provider}' is explicitly untrusted"],
                          KnowledgeStatus.REJECTED, candidate.verification)

        if not qualifying and any(m in low for m in _SPECULATION_MARKERS):
            return result(PromotionOutcome.REJECT, ["unsupported speculation without evidence"],
                          KnowledgeStatus.REJECTED, candidate.verification)

        if not qualifying and candidate.confidence < self._reject_confidence:
            return result(
                PromotionOutcome.REJECT,
                [f"confidence {candidate.confidence:.2f} below {self._reject_confidence:.2f} and unsupported"],
                KnowledgeStatus.REJECTED, candidate.verification)

        # ---------------- Risk assessment ----------------
        sens = self._detect_sensitivity(low, candidate, security, financial, legal, research)
        time_sensitive = candidate.durability == Durability.TIME_SENSITIVE
        contradictory = bool(candidate.conflicts_with)
        changes_accepted = candidate.supersedes is not None
        provider_only_rec = (
            candidate.kind in self._RECOMMENDATION_KINDS and not qualifying and not has_approval
        )

        review_needed = (
            candidate.kind in self._REVIEW_ALWAYS_KINDS
            or sens.any
            or time_sensitive
            or contradictory
            or changes_accepted
            or provider_only_rec
        )

        # ---------------- REVIEW-required (§4) ----------------
        if review_needed:
            reasons, reviewer, priority = self._review_reasons(
                candidate, sens, time_sensitive, contradictory, changes_accepted, provider_only_rec
            )
            review_after = (
                suggest_review_after(now, Durability.TIME_SENSITIVE) if time_sensitive
                else suggest_review_after(now, candidate.durability)
            )
            if has_approval:
                # A workflow/human already vetted this review-required item.
                verification = (
                    VerificationStatus.HUMAN_APPROVED if human_appr
                    else VerificationStatus.WORKFLOW_APPROVED
                )
                return result(
                    PromotionOutcome.REVIEWED,
                    ["approved via " + ("human sign-off" if human_appr else "team workflow")] + reasons,
                    KnowledgeStatus.REVIEWED, verification, conf=0.1,
                    reviewer=reviewer, priority=ReviewPriority.LOW, review_after=review_after)
            return result(
                PromotionOutcome.REQUIRE_HUMAN_REVIEW, reasons,
                KnowledgeStatus.CANDIDATE, VerificationStatus.UNVERIFIED,
                reviewer=reviewer, priority=priority, review_after=review_after)

        # ---------------- AUTO-ACCEPT (§3) ----------------
        if qualifying:
            verification = self._best_verification(test_backed, approved_files, workflow_appr, human_appr)
            reasons = ["low-risk and evidence-backed: " + summary,
                       "not security/financial/legal/research, not time-sensitive, not contradictory"]
            return result(
                PromotionOutcome.AUTO_ACCEPT, reasons,
                KnowledgeStatus.ACCEPTED, verification, conf=0.2,
                review_after=suggest_review_after(now, candidate.durability))

        # ---------------- DEFER pending evidence ----------------
        return result(
            PromotionOutcome.DEFER,
            ["plausible and low-risk but lacks verifying evidence; deferred pending test/build/approval"],
            KnowledgeStatus.CANDIDATE, VerificationStatus.UNVERIFIED,
            reviewer="", priority=ReviewPriority.LOW)

    def safe_validate(
        self,
        candidate: KnowledgeRecord,
        evidence: list[Evidence] | None = None,
        *,
        now: Timestamp | None = None,
        **flags: bool,
    ) -> ValidationResult:
        """Validate without ever raising (§8/§9).

        A validation bug must not break the provider-response path. Any error
        degrades to a safe REQUIRE_HUMAN_REVIEW outcome that leaves the
        candidate unaccepted.
        """
        try:
            return self.validate(candidate, evidence, now=now, **flags)
        except Exception as exc:  # noqa: BLE001 — deliberately defensive
            return ValidationResult(
                outcome=PromotionOutcome.REQUIRE_HUMAN_REVIEW,
                reasons=[f"validation error, held for review: {type(exc).__name__}: {exc}"],
                resulting_status=KnowledgeStatus.CANDIDATE,
                resulting_verification=VerificationStatus.UNVERIFIED,
                required_reviewer="human",
                review_priority=ReviewPriority.HIGH,
                evidence_summary="validation failed",
                decided_at=now or datetime.now(tz=timezone.utc),
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _contains_secret(self, text: str) -> bool:
        return any(p.search(text) for p in _SECRET_PATTERNS)

    def _has_provenance(self, candidate: KnowledgeRecord, evidence: list[Evidence]) -> bool:
        return bool(
            candidate.source_provider
            or candidate.source_run
            or candidate.source_task
            or candidate.citations
            or evidence
        )

    def _detect_sensitivity(
        self,
        low: str,
        candidate: KnowledgeRecord,
        security: bool | None,
        financial: bool | None,
        legal: bool | None,
        research: bool | None,
    ) -> Sensitivity:
        has_external_citation = any(c.url for c in candidate.citations)
        return Sensitivity(
            security=security if security is not None else bool(_SECURITY_RE.search(low)),
            financial=financial if financial is not None else bool(_FINANCIAL_RE.search(low)),
            legal=legal if legal is not None else bool(_LEGAL_RE.search(low)),
            research=(
                research if research is not None
                else has_external_citation or bool(_RESEARCH_RE.search(low))
            ),
        )

    def _review_reasons(
        self, candidate, sens, time_sensitive, contradictory, changes_accepted, provider_only_rec
    ) -> tuple[list[str], str, ReviewPriority]:
        reasons: list[str] = []
        reviewer = "human"
        priority = ReviewPriority.MEDIUM
        if candidate.kind == KnowledgeKind.DECISION:
            reasons.append("architecture/design decision requires human review")
            reviewer = "architect"
        if sens.security:
            reasons.append("security-sensitive finding requires review")
            reviewer, priority = "security", ReviewPriority.HIGH
        if sens.financial:
            reasons.append("financial statement requires review")
            reviewer, priority = "finance", ReviewPriority.HIGH
        if sens.legal:
            reasons.append("legal statement requires review")
            reviewer, priority = "legal", ReviewPriority.HIGH
        if sens.research:
            reasons.append("external research claim requires review")
            reviewer = reviewer if reviewer != "human" else "research"
        if time_sensitive:
            reasons.append("time-sensitive/current information needs a review window")
        if contradictory:
            reasons.append("conflicts with existing knowledge; needs reconciliation")
            priority = ReviewPriority.HIGH
        if changes_accepted:
            reasons.append("changes an existing accepted decision")
            reviewer = "architect" if reviewer == "human" else reviewer
        if provider_only_rec:
            reasons.append("provider-only recommendation without evidence")
        return reasons, reviewer, priority

    def _best_verification(self, test_backed, approved_files, workflow_appr, human_appr) -> VerificationStatus:
        if human_appr:
            return VerificationStatus.HUMAN_APPROVED
        if workflow_appr:
            return VerificationStatus.WORKFLOW_APPROVED
        return VerificationStatus.TEST_BACKED  # test_backed or approved_files

    def _evidence_summary(self, evidence: list[Evidence]) -> str:
        if not evidence:
            return "no evidence provided"
        return f"{len(evidence)} evidence item(s): " + ", ".join(e.describe() for e in evidence)


# ---------------------------------------------------------------------------
# Pipeline integration (§8)
# ---------------------------------------------------------------------------


# Supplies evidence for candidate index i / record r → list[Evidence].
EvidenceProvider = Callable[[int, KnowledgeRecord], "list[Evidence]"]


@dataclass
class ValidatedCandidate:
    """A candidate paired with its validation result after the §8 flow."""

    record: KnowledgeRecord
    result: ValidationResult


def validate_extraction(
    extraction: ExtractionResult,
    validator: KnowledgeValidator,
    *,
    evidence_for: EvidenceProvider | None = None,
    apply: bool = True,
    now: Timestamp | None = None,
) -> list[ValidatedCandidate]:
    """Run extraction candidates through validation and update their status (§8).

    Flow: extraction result → validation → candidate status update. Uses
    ``safe_validate`` so a validation failure never breaks the caller's response
    path — a broken candidate degrades to REQUIRE_HUMAN_REVIEW, the rest proceed.

    Nothing is persisted here. Accepted records only reach disk through the
    explicit ``KnowledgeStore`` seam, which the caller invokes separately.
    """
    now = now or datetime.now(tz=timezone.utc)
    out: list[ValidatedCandidate] = []
    for i, record in enumerate(extraction.candidates):
        evidence = evidence_for(i, record) if evidence_for else None
        result = validator.safe_validate(record, evidence, now=now)
        if apply:
            result.apply(record, now=now)
        out.append(ValidatedCandidate(record=record, result=result))
    return out
