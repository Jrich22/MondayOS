"""Tests for §5 conflict detection and merge/supersede proposals.

Covers the §10 matrix: exact/near duplicates, project isolation and the
organizational-knowledge exception, obvious vs ambiguous contradictions,
supersession (stale / stronger evidence / never-auto-supersede protected
kinds), merge-proposal contents, exclusion of rejected/superseded records,
the NEW fallback, the deterministic no-provider guarantee, and resilience.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from retention import (
    ConflictDetector,
    ConflictOutcome,
    DeterministicMatcher,
    Durability,
    KnowledgeKind,
    KnowledgeRecord,
    KnowledgeStatus,
    VerificationStatus,
)

NOW = datetime(2026, 7, 14, tzinfo=timezone.utc)


def rec(knowledge_id, statement, kind=KnowledgeKind.CLAIM, **overrides) -> KnowledgeRecord:
    defaults = dict(
        knowledge_id=knowledge_id,
        kind=kind,
        title=statement[:50],
        canonical_statement=statement,
        summary="",
        project="mondayos",
        tags=[],
        source_provider="anthropic",
        source_run="wf-1",
        confidence=0.8,
        verification=VerificationStatus.TEST_BACKED,
        status=KnowledgeStatus.ACCEPTED,
        durability=Durability.DURABLE,
        created_at=NOW,
        last_verified_at=NOW,
    )
    defaults.update(overrides)
    return KnowledgeRecord(**defaults)


@pytest.fixture
def detector() -> ConflictDetector:
    return ConflictDetector()


# ---------------------------------------------------------------------------
# Duplicates (§3, §4)
# ---------------------------------------------------------------------------


def test_exact_duplicate_detected(detector):
    existing = [rec("K-1", "The API binds to localhost by default.")]
    r = detector.detect(rec("", "The API binds to localhost by default."), existing, NOW)
    assert r.outcome == ConflictOutcome.EXACT_DUPLICATE
    assert r.matched_record_ids == ["K-1"]
    assert r.merge_proposal is not None


def test_punctuation_and_case_duplicate_detected(detector):
    existing = [rec("K-1", "Cue App separates Person from Guest.")]
    r = detector.detect(rec("", "the CUE app  separates person from GUEST!!!"), existing, NOW)
    assert r.outcome == ConflictOutcome.EXACT_DUPLICATE


def test_near_duplicate_proposed_for_merge(detector):
    existing = [rec("K-1", "The API binds to localhost by default.")]
    cand = rec("", "The API binds to localhost by default when no host is configured.")
    r = detector.detect(cand, existing, NOW)
    assert r.outcome == ConflictOutcome.MERGE_PROPOSED
    assert r.merge_proposal.target_record_id == "K-1"


def test_no_match_returns_new(detector):
    existing = [rec("K-1", "The API binds to localhost by default.")]
    r = detector.detect(rec("", "Cue App uses IndexedDB for offline guest storage."), existing, NOW)
    assert r.outcome == ConflictOutcome.NEW


# ---------------------------------------------------------------------------
# Project isolation (§9)
# ---------------------------------------------------------------------------


def test_different_project_not_merged(detector):
    existing = [rec("K-1", "The API binds to localhost by default.", project="mondayos")]
    cand = rec("", "The API binds to localhost by default.", project="cue-app")
    r = detector.detect(cand, existing, NOW)
    assert r.outcome == ConflictOutcome.NEW
    assert r.matched_record_ids == []


def test_organizational_knowledge_matches_across_projects(detector):
    org = rec("DOC-1", "The API binds to localhost by default.", project="mondayos",
              metadata={"origin": "mks"})
    cand = rec("", "The API binds to localhost by default.", project="cue-app")
    r = detector.detect(cand, [org], NOW)
    assert r.outcome == ConflictOutcome.EXACT_DUPLICATE
    assert r.matched_record_ids == ["DOC-1"]


# ---------------------------------------------------------------------------
# Contradictions (§5)
# ---------------------------------------------------------------------------


def test_obvious_antonym_contradiction_detected(detector):
    existing = [rec("K-2", "Telemetry is enabled by default.")]
    r = detector.detect(rec("", "Telemetry is disabled by default."), existing, NOW)
    assert r.outcome == ConflictOutcome.CONFLICT_REVIEW
    assert any("antonym" in s for s in r.contradiction_signals)


def test_must_vs_must_not_contradiction_detected(detector):
    existing = [rec("K-5", "The migration must run before the app boots.", kind=KnowledgeKind.REQUIREMENT)]
    cand = rec("", "The migration must not run before the app boots.", kind=KnowledgeKind.REQUIREMENT)
    r = detector.detect(cand, existing, NOW)
    assert r.outcome == ConflictOutcome.CONFLICT_REVIEW
    assert any("negation" in s for s in r.contradiction_signals)


def test_ambiguous_value_mismatch_routed_to_review(detector):
    existing = [rec("K-6", "The service pins Python 3.11.")]
    cand = rec("", "The service pins Python 3.12.")
    r = detector.detect(cand, existing, NOW)
    assert r.outcome == ConflictOutcome.CONFLICT_REVIEW
    assert any("value mismatch" in s for s in r.contradiction_signals)
    assert r.review_priority.value == "medium"  # ambiguous → medium, not high


def test_declared_conflict_routed_to_review(detector):
    existing = [rec("K-7", "Writes go through a single global lock.")]
    cand = rec("", "Writes use per-partition locks for throughput.", conflicts_with=["K-7"])
    r = detector.detect(cand, existing, NOW)
    assert r.outcome == ConflictOutcome.CONFLICT_REVIEW


# ---------------------------------------------------------------------------
# Supersession (§6)
# ---------------------------------------------------------------------------


def test_stale_record_supersession_proposed(detector):
    stale = rec("K-3", "The nightly job processes 5000 records.",
                durability=Durability.TIME_SENSITIVE,
                created_at=NOW - timedelta(days=60), last_verified_at=NOW - timedelta(days=60))
    cand = rec("", "The nightly job processes 8000 records.", durability=Durability.TIME_SENSITIVE)
    r = detector.detect(cand, [stale], NOW)
    assert r.outcome == ConflictOutcome.SUPERSEDE_PROPOSED
    assert "stale" in r.supersede_proposal.rationale


def test_stronger_evidence_supersession_proposed(detector):
    weak = rec("K-8", "The API rate limit is 100 requests per second.",
               verification=VerificationStatus.UNVERIFIED)
    strong = rec("", "The API rate limit is 120 requests per second.",
                 verification=VerificationStatus.WORKFLOW_APPROVED)
    r = detector.detect(strong, [weak], NOW)
    assert r.outcome == ConflictOutcome.SUPERSEDE_PROPOSED
    assert "stronger provenance" in r.supersede_proposal.rationale


def test_architecture_decision_never_auto_superseded(detector):
    decision = rec("DEC-2", "We chose Postgres as the primary backend.", kind=KnowledgeKind.DECISION)
    cand = rec("", "We now use DynamoDB as the primary backend.", kind=KnowledgeKind.DECISION,
               supersedes="DEC-2")
    r = detector.detect(cand, [decision], NOW)
    assert r.outcome == ConflictOutcome.CONFLICT_REVIEW  # not SUPERSEDE_PROPOSED
    assert r.supersede_proposal is not None
    assert r.supersede_proposal.review_required is True
    assert r.review_priority.value == "high"


def test_human_approved_principle_never_auto_superseded(detector):
    principle = rec("K-9", "The nightly job processes 5000 records.",
                    verification=VerificationStatus.HUMAN_APPROVED,
                    durability=Durability.TIME_SENSITIVE,
                    created_at=NOW - timedelta(days=90), last_verified_at=NOW - timedelta(days=90))
    cand = rec("", "The nightly job processes 9000 records.")
    r = detector.detect(cand, [principle], NOW)
    assert r.outcome == ConflictOutcome.CONFLICT_REVIEW


# ---------------------------------------------------------------------------
# Merge proposal contents (§7)
# ---------------------------------------------------------------------------


def test_merge_proposal_combines_provenance_and_strongest_fields(detector):
    from retention import Citation

    # Candidate is a near-duplicate (not stronger, not stale) so it merges
    # rather than supersedes. Target holds the stronger verification; candidate
    # holds the higher confidence — the proposal keeps the strongest of each.
    existing = [rec("K-1", "The API binds to localhost by default.",
                    confidence=0.7, verification=VerificationStatus.WORKFLOW_APPROVED,
                    source_provider="openai", citations=[Citation(source="test_api.py")])]
    cand = rec("", "The API binds to localhost by default when unspecified.",
               confidence=0.9, verification=VerificationStatus.TEST_BACKED,
               source_provider="anthropic", citations=[Citation(source="review-9")])
    r = detector.detect(cand, existing, NOW)
    assert r.outcome == ConflictOutcome.MERGE_PROPOSED
    mp = r.merge_proposal
    assert mp.strongest_confidence == 0.9  # candidate's higher confidence
    assert mp.strongest_verification == VerificationStatus.WORKFLOW_APPROVED  # target's stronger verification
    providers = {p["provider"] for p in mp.combined_provenance}
    assert providers == {"openai", "anthropic"}
    assert len(mp.preserved_evidence) == 2  # both citations preserved
    assert "confidence" in mp.fields_changed and "provenance" in mp.fields_changed


# ---------------------------------------------------------------------------
# Exclusions, determinism, resilience (§10)
# ---------------------------------------------------------------------------


def test_rejected_record_excluded_from_matching(detector):
    rejected = rec("K-1", "The API binds to localhost by default.", status=KnowledgeStatus.REJECTED)
    r = detector.detect(rec("", "The API binds to localhost by default."), [rejected], NOW)
    assert r.outcome == ConflictOutcome.NEW  # rejected record is invisible


def test_superseded_record_excluded_from_matching(detector):
    old = rec("K-1", "The API binds to localhost by default.", status=KnowledgeStatus.SUPERSEDED)
    r = detector.detect(rec("", "The API binds to localhost by default."), [old], NOW)
    assert r.outcome == ConflictOutcome.NEW


def test_candidate_not_superseded_only_accepted_is(detector):
    # A matching but not-yet-accepted candidate is a near/exact dup, never a
    # supersession target.
    other_candidate = rec("K-1", "The nightly job processes 5000 records.",
                          status=KnowledgeStatus.CANDIDATE)
    cand = rec("", "The nightly job processes 5000 records.")
    r = detector.detect(cand, [other_candidate], NOW)
    assert r.outcome == ConflictOutcome.EXACT_DUPLICATE


def test_deterministic_matcher_requires_no_provider_call():
    # The detector uses only the lexical matcher; constructing and running it
    # touches no provider/network. A stub matcher proves the seam is honored.
    class CountingMatcher(DeterministicMatcher):
        calls = 0

        def similarity(self, candidate, record):
            CountingMatcher.calls += 1
            return super().similarity(candidate, record)

    d = ConflictDetector(matcher=CountingMatcher())
    d.detect(rec("", "The API binds to localhost by default."),
             [rec("K-1", "The API binds to localhost by default.")], NOW)
    assert CountingMatcher.calls == 1  # exactly one lexical comparison, no provider


def test_detection_failure_never_breaks_the_path():
    class Boom(ConflictDetector):
        def detect(self, *a, **k):
            raise RuntimeError("simulated conflict bug")

    r = Boom().detect_safe(rec("", "x claim"), [rec("K-1", "y claim")], NOW)
    assert r.outcome == ConflictOutcome.CONFLICT_REVIEW
    assert "error" in r.explanation


def test_empty_existing_returns_new(detector):
    r = detector.detect(rec("", "A brand new observation about the loader."), [], NOW)
    assert r.outcome == ConflictOutcome.NEW
    assert r.similarity_scores == {}
