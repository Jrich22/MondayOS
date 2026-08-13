"""Tests for the v4.0 memory-first pipeline (§1) and knowledge records (§3).

Covers the subset of the §10 test matrix relevant to what this increment
builds: memory hit avoids a provider call, weak match triggers a provider,
stale entry triggers verification, contradiction escalates, rejected records
are never used, project isolation, source provenance, cost tracking, and
usage-count updates — plus record serialization and freshness/staleness.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from retention import (
    Decision,
    Durability,
    KnowledgeKind,
    KnowledgeRecord,
    KnowledgeStatus,
    MemoryFirstPipeline,
    ReasoningRequest,
    RecordSource,
    RequestKind,
    VerificationStatus,
    classify,
)
from retention.pipeline import DecisionLog

NOW = datetime(2026, 7, 14, tzinfo=timezone.utc)


def make_record(**overrides) -> KnowledgeRecord:
    """Build an accepted, high-trust, fresh record; override fields per test."""
    defaults = dict(
        knowledge_id="K-1",
        kind=KnowledgeKind.DECISION,
        title="Knowledge storage backend",
        canonical_statement="MondayOS stores knowledge as Markdown files on disk",
        summary="Phase 1 backend choice",
        project="mondayos",
        tags=["storage", "backend", "knowledge"],
        source_provider="human",
        confidence=0.95,
        verification=VerificationStatus.HUMAN_APPROVED,
        status=KnowledgeStatus.ACCEPTED,
        durability=Durability.DURABLE,
        created_at=NOW,
        last_verified_at=NOW,
    )
    defaults.update(overrides)
    return KnowledgeRecord(**defaults)


def pipeline_with(*records: KnowledgeRecord) -> MemoryFirstPipeline:
    return MemoryFirstPipeline([RecordSource(list(records))])


def storage_request(**overrides) -> ReasoningRequest:
    defaults = dict(text="what backend do we use for knowledge storage", project="mondayos")
    defaults.update(overrides)
    return ReasoningRequest(**defaults)


# ---------------------------------------------------------------------------
# §3 — structured knowledge record
# ---------------------------------------------------------------------------


def test_record_serialization_roundtrip():
    rec = make_record(tags=["a", "b"], conflicts_with=["K-9"])
    assert KnowledgeRecord.from_dict(rec.to_dict()).to_dict() == rec.to_dict()


def test_durable_record_has_no_review_deadline():
    rec = make_record(durability=Durability.DURABLE)
    assert rec.review_after is None
    assert rec.needs_review(NOW) is False


def test_time_sensitive_record_gets_short_review_window():
    rec = make_record(durability=Durability.TIME_SENSITIVE, created_at=NOW)
    assert rec.review_after == NOW + timedelta(days=14)


def test_accepted_record_past_review_window_is_stale():
    rec = make_record(durability=Durability.TIME_SENSITIVE, created_at=NOW - timedelta(days=30))
    assert rec.is_stale(NOW) is True


def test_mark_verified_refreshes_review_window_and_revives_stale():
    rec = make_record(status=KnowledgeStatus.STALE, durability=Durability.VERSION_BOUND)
    rec.mark_verified(VerificationStatus.TEST_BACKED, now=NOW)
    assert rec.status == KnowledgeStatus.ACCEPTED
    assert rec.last_verified_at == NOW
    assert rec.review_after == NOW + timedelta(days=90)


# ---------------------------------------------------------------------------
# §10 — pipeline behavior
# ---------------------------------------------------------------------------


def test_memory_hit_avoids_provider_call():
    pipe = pipeline_with(make_record())
    d = pipe.decide(storage_request(), now=NOW)
    assert d.decision == Decision.ANSWER_FROM_MEMORY
    assert d.answer_source == "memory"
    assert d.fallback_reason == ""
    assert d.cost_avoided_usd > 0 and d.tokens_avoided > 0


def test_weak_match_triggers_provider():
    pipe = pipeline_with(make_record())
    d = pipe.decide(ReasoningRequest(text="how do I configure the office coffee machine"), now=NOW)
    assert d.decision == Decision.CALL_EXTERNAL_MODEL
    assert d.answer_source == "external"
    assert d.fallback_reason in ("weak match", "no relevant knowledge")
    assert d.cost_avoided_usd == 0.0


def test_no_knowledge_triggers_provider():
    pipe = MemoryFirstPipeline([RecordSource([])])
    d = pipe.decide(storage_request(), now=NOW)
    assert d.decision == Decision.CALL_EXTERNAL_MODEL
    assert d.fallback_reason == "no relevant knowledge"


def test_stale_entry_triggers_verification():
    stale = make_record(
        durability=Durability.TIME_SENSITIVE,
        created_at=NOW - timedelta(days=60),
        last_verified_at=NOW - timedelta(days=60),
    )
    pipe = pipeline_with(stale)
    d = pipe.decide(storage_request(), now=NOW)
    assert d.decision == Decision.ANSWER_WITH_VERIFICATION
    assert d.fallback_reason == "verification"
    # Verification still avoids the bulk of a full generation.
    assert d.cost_avoided_usd > 0


def test_low_confidence_triggers_verification():
    weak = make_record(confidence=0.4)
    pipe = pipeline_with(weak)
    d = pipe.decide(storage_request(), now=NOW)
    assert d.decision == Decision.ANSWER_WITH_VERIFICATION


def test_unverified_candidate_is_not_answered_directly():
    # A candidate (not yet accepted) is relevant but not trusted → verify.
    cand = make_record(status=KnowledgeStatus.CANDIDATE, verification=VerificationStatus.UNVERIFIED)
    pipe = pipeline_with(cand)
    d = pipe.decide(storage_request(), now=NOW)
    assert d.decision == Decision.ANSWER_WITH_VERIFICATION


def test_contradiction_creates_multi_model_escalation():
    a = make_record(knowledge_id="K-1", conflicts_with=["K-2"])
    b = make_record(
        knowledge_id="K-2",
        conflicts_with=["K-1"],
        canonical_statement="MondayOS stores knowledge in a Postgres knowledge database",
        summary="alternate storage backend claim",
    )
    pipe = pipeline_with(a, b)
    d = pipe.decide(storage_request(), now=NOW)
    assert d.decision == Decision.ESCALATE_MULTI_MODEL
    assert d.fallback_reason == "conflicting knowledge"


def test_rejected_candidate_is_never_used():
    rejected = make_record(status=KnowledgeStatus.REJECTED)
    pipe = pipeline_with(rejected)
    d = pipe.decide(storage_request(), now=NOW)
    # No usable record → must fall back to a provider, not answer from memory.
    assert d.decision == Decision.CALL_EXTERNAL_MODEL
    assert d.matches == []


def test_superseded_record_is_never_used():
    superseded = make_record(status=KnowledgeStatus.SUPERSEDED)
    pipe = pipeline_with(superseded)
    d = pipe.decide(storage_request(), now=NOW)
    assert d.decision == Decision.CALL_EXTERNAL_MODEL


def test_project_isolation():
    other = make_record(project="cue-app")
    pipe = pipeline_with(other)
    d = pipe.decide(storage_request(project="mondayos"), now=NOW)
    # The mondayos request must not see cue-app knowledge.
    assert d.matches == []
    assert d.decision == Decision.CALL_EXTERNAL_MODEL


def test_privacy_sensitive_routes_to_local_not_external():
    pipe = MemoryFirstPipeline([RecordSource([])])
    d = pipe.decide(storage_request(privacy_sensitive=True), now=NOW)
    assert d.decision == Decision.CALL_LOCAL_MODEL
    assert d.answer_source == "local"


def test_accepted_decision_is_retrieved_for_future_task():
    rec = make_record()
    pipe = pipeline_with(rec)
    d = pipe.decide(storage_request(), now=NOW)
    assert d.best_match is not None
    assert d.best_match.record.knowledge_id == "K-1"


def test_usage_count_increments_when_record_answers():
    rec = make_record()
    pipe = pipeline_with(rec)
    assert rec.usage_count == 0
    pipe.decide(storage_request(), now=NOW)
    assert rec.usage_count == 1


def test_source_provenance_preserved_on_match():
    rec = make_record(source_provider="anthropic", source_model="claude-opus-4-8", source_run="wf-42")
    pipe = pipeline_with(rec)
    d = pipe.decide(storage_request(), now=NOW)
    matched = d.best_match.record
    assert matched.source_provider == "anthropic"
    assert matched.source_model == "claude-opus-4-8"
    assert matched.source_run == "wf-42"


# ---------------------------------------------------------------------------
# §1/§8 — cost-avoidance logging
# ---------------------------------------------------------------------------


def test_decision_log_accumulates_savings_and_rates():
    log = DecisionLog()
    pipe = MemoryFirstPipeline([RecordSource([make_record()])], log=log)
    pipe.decide(storage_request(), now=NOW)                      # memory hit
    pipe.decide(ReasoningRequest(text="unrelated question about lunch"), now=NOW)  # provider

    assert len(log.entries) == 2
    assert log.total_cost_avoided_usd > 0
    assert log.total_tokens_avoided > 0
    assert log.memory_answer_rate == 0.5
    assert log.provider_avoidance_rate == 0.5


def test_empty_log_rates_are_zero():
    log = DecisionLog()
    assert log.memory_answer_rate == 0.0
    assert log.provider_avoidance_rate == 0.0


# ---------------------------------------------------------------------------
# §1 — classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("should we choose an event-driven architecture", RequestKind.DECISION),
        ("what is the latest pricing for the api", RequestKind.VOLATILE),
        ("is there a security vulnerability in auth", RequestKind.SECURITY),
        ("how do I deploy the dashboard", RequestKind.HOWTO),
        ("refactor this function to fix the bug", RequestKind.CODE),
    ],
)
def test_classification(text, expected):
    assert classify(ReasoningRequest(text=text)) == expected


def test_volatile_kind_requires_verification_even_when_fresh():
    # A perfectly fresh, accepted, high-confidence record about pricing must
    # still be verified because the request is volatile (§4/§9).
    rec = make_record(
        title="API pricing", canonical_statement="Opus costs $15 per million output tokens",
        summary="current pricing", tags=["pricing", "api", "cost"], durability=Durability.TIME_SENSITIVE,
    )
    pipe = pipeline_with(rec)
    d = pipe.decide(ReasoningRequest(text="what is the current api pricing cost"), now=NOW)
    assert d.classification == RequestKind.VOLATILE
    assert d.decision == Decision.ANSWER_WITH_VERIFICATION
