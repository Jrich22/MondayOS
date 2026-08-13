"""Tests for §4 validation and promotion rules.

Covers the §9 matrix: auto-accept of test-backed deterministic facts, review
routing per kind/sensitivity, the rejection categories, defer-pending-evidence,
evidence-model behavior, explanation preservation, the extraction→validation
integration, and the guarantee that a validation failure never breaks the
provider-response path.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from retention import (
    Citation,
    Durability,
    Evidence,
    EvidenceType,
    ExtractionResult,
    KnowledgeExtractor,
    KnowledgeKind,
    KnowledgeRecord,
    KnowledgeStatus,
    KnowledgeValidator,
    PromotionOutcome,
    ReviewPriority,
    VerificationStatus,
    validate_extraction,
)

NOW = datetime(2026, 7, 14, tzinfo=timezone.utc)


def cand(statement: str, kind: KnowledgeKind = KnowledgeKind.CLAIM, **overrides) -> KnowledgeRecord:
    defaults = dict(
        knowledge_id="",
        kind=kind,
        title=statement[:60],
        canonical_statement=statement,
        summary="extracted from anthropic response",
        project="mondayos",
        tags=[],
        source_provider="anthropic",
        source_model="claude-opus-4-8",
        source_run="wf-1",
        confidence=0.5,
        verification=VerificationStatus.UNVERIFIED,
        status=KnowledgeStatus.CANDIDATE,
        durability=Durability.DURABLE,
        created_at=NOW,
    )
    defaults.update(overrides)
    return KnowledgeRecord(**defaults)


@pytest.fixture
def validator() -> KnowledgeValidator:
    return KnowledgeValidator()


def make_test_evidence(source="pytest", count=1033):
    return Evidence(EvidenceType.TEST_RESULT, source=source, result="pass", test_count=count)


# ---------------------------------------------------------------------------
# Auto-accept (§3)
# ---------------------------------------------------------------------------


def test_test_backed_deterministic_fact_auto_accepted(validator):
    r = validator.validate(cand("The full suite passed 1033 tests."), [make_test_evidence()], now=NOW)
    assert r.outcome == PromotionOutcome.AUTO_ACCEPT
    assert r.resulting_status == KnowledgeStatus.ACCEPTED
    assert r.resulting_verification == VerificationStatus.TEST_BACKED
    assert r.confidence_adjustment > 0


def test_approved_project_file_fact_auto_accepted(validator):
    ev = [Evidence(EvidenceType.APPROVED_FILE, source="projects/cue-app/src/lib/store.ts")]
    r = validator.validate(cand("Cue App separates Person from Guest."), ev, now=NOW)
    assert r.outcome == PromotionOutcome.AUTO_ACCEPT


def test_validated_bug_fix_accepted(validator):
    ev = [make_test_evidence(source="test_store.py", count=4)]
    r = validator.validate(
        cand("The sequence file corruption was caused by a missing lock; the fix adds a mutex.",
             kind=KnowledgeKind.LESSON),
        ev, now=NOW,
    )
    assert r.outcome == PromotionOutcome.AUTO_ACCEPT


def test_localhost_binding_fact_auto_accepted(validator):
    r = validator.validate(cand("The API binds to localhost by default."), [make_test_evidence(count=1)], now=NOW)
    assert r.outcome == PromotionOutcome.AUTO_ACCEPT


# ---------------------------------------------------------------------------
# Review required (§4)
# ---------------------------------------------------------------------------


def test_architecture_decision_requires_human_review(validator):
    r = validator.validate(cand("We chose an event-driven architecture.", kind=KnowledgeKind.DECISION), now=NOW)
    assert r.outcome == PromotionOutcome.REQUIRE_HUMAN_REVIEW
    assert r.resulting_status == KnowledgeStatus.CANDIDATE
    assert r.required_reviewer == "architect"


def test_approved_workflow_decision_marked_reviewed(validator):
    ev = [Evidence(EvidenceType.WORKFLOW_APPROVAL, source="review-9", approval_run="wf-9")]
    r = validator.validate(cand("We standardized on Postgres.", kind=KnowledgeKind.DECISION), ev, now=NOW)
    assert r.outcome == PromotionOutcome.REVIEWED
    assert r.resulting_status == KnowledgeStatus.REVIEWED
    assert r.resulting_verification == VerificationStatus.WORKFLOW_APPROVED


def test_security_finding_requires_review(validator):
    r = validator.validate(cand("There is an auth bypass vulnerability in login.", kind=KnowledgeKind.RISK), now=NOW)
    assert r.outcome == PromotionOutcome.REQUIRE_HUMAN_REVIEW
    assert r.required_reviewer == "security"
    assert r.review_priority == ReviewPriority.HIGH


def test_cited_external_research_requires_review(validator):
    c = cand("Opus leads the SWE-bench benchmark.", citations=[Citation(source="blog", url="https://x/y")])
    r = validator.validate(c, now=NOW)
    assert r.outcome == PromotionOutcome.REQUIRE_HUMAN_REVIEW
    assert r.required_reviewer == "research"


def test_time_sensitive_fact_gets_review_window(validator):
    c = cand("Opus pricing is $15 per million output tokens.", durability=Durability.TIME_SENSITIVE)
    r = validator.validate(c, now=NOW)
    assert r.outcome == PromotionOutcome.REQUIRE_HUMAN_REVIEW
    assert r.review_after is not None


def test_contradiction_requires_review(validator):
    c = cand("The backend is SQLite.", conflicts_with=["K-77"])
    r = validator.validate(c, [make_test_evidence(count=1)], now=NOW)  # even with evidence
    assert r.outcome == PromotionOutcome.REQUIRE_HUMAN_REVIEW
    assert r.review_priority == ReviewPriority.HIGH


def test_change_to_accepted_decision_requires_review(validator):
    c = cand("Switch the backend to DynamoDB.", kind=KnowledgeKind.PATTERN, supersedes="DEC-0002")
    r = validator.validate(c, now=NOW)
    assert r.outcome == PromotionOutcome.REQUIRE_HUMAN_REVIEW


def test_provider_only_pattern_without_evidence_requires_review(validator):
    r = validator.validate(cand("Always wrap writes in a transaction.", kind=KnowledgeKind.PATTERN), now=NOW)
    assert r.outcome == PromotionOutcome.REQUIRE_HUMAN_REVIEW


# ---------------------------------------------------------------------------
# Rejection (§5)
# ---------------------------------------------------------------------------


def test_secret_bearing_candidate_rejected(validator):
    r = validator.validate(cand("The key is sk-ant-verysecretlongtokenvalue1234."), now=NOW)
    assert r.outcome == PromotionOutcome.REJECT
    assert r.resulting_status == KnowledgeStatus.REJECTED


def test_low_confidence_claim_rejected(validator):
    r = validator.validate(cand("The cache is warm.", confidence=0.2), now=NOW)
    assert r.outcome == PromotionOutcome.REJECT


def test_unsupported_speculation_rejected(validator):
    r = validator.validate(cand("Maybe the leak is in the parser, not sure."), now=NOW)
    assert r.outcome == PromotionOutcome.REJECT


def test_malformed_provenance_rejected(validator):
    c = cand("Some claim with no provenance.", source_provider="", source_run="", source_task="")
    r = validator.validate(c, now=NOW)  # no evidence, no citations either
    assert r.outcome == PromotionOutcome.REJECT
    assert "provenance" in r.reasons[0]


def test_malformed_empty_statement_rejected(validator):
    c = cand("placeholder")
    c.canonical_statement = "   "
    r = validator.validate(c, now=NOW)
    assert r.outcome == PromotionOutcome.REJECT


def test_temporary_status_rejected(validator):
    r = validator.validate(cand("Currently the CI pipeline is running slowly."), now=NOW)
    assert r.outcome == PromotionOutcome.REJECT


def test_evidence_contradicting_claim_rejected(validator):
    ev = [Evidence(EvidenceType.CONTRADICTION, source="pytest", result="fail")]
    r = validator.validate(cand("The suite is fully green."), ev, now=NOW)
    assert r.outcome == PromotionOutcome.REJECT
    assert r.resulting_verification == VerificationStatus.CONTRADICTED


def test_untrusted_source_rejected():
    v = KnowledgeValidator(untrusted_sources=frozenset({"sketchy"}))
    r = v.validate(cand("A claim.", source_provider="sketchy"), [make_test_evidence(count=1)], now=NOW)
    assert r.outcome == PromotionOutcome.REJECT


# ---------------------------------------------------------------------------
# Defer (§2)
# ---------------------------------------------------------------------------


def test_plausible_claim_without_evidence_is_deferred(validator):
    r = validator.validate(cand("The loader validates frontmatter before indexing.", confidence=0.5), now=NOW)
    assert r.outcome == PromotionOutcome.DEFER
    assert r.resulting_status == KnowledgeStatus.CANDIDATE


# ---------------------------------------------------------------------------
# Evidence model (§6) & explanation (§7)
# ---------------------------------------------------------------------------


def test_failing_test_result_does_not_count_as_backing(validator):
    ev = [Evidence(EvidenceType.TEST_RESULT, source="pytest", result="fail", test_count=3)]
    # A failing test is a contradiction → reject, not accept.
    r = validator.validate(cand("The suite passes."), ev, now=NOW)
    assert r.outcome == PromotionOutcome.REJECT


def test_zero_count_test_result_is_not_backing(validator):
    ev = [Evidence(EvidenceType.TEST_RESULT, source="pytest", result="pass", test_count=0)]
    r = validator.validate(cand("The loader validates frontmatter first."), ev, now=NOW)
    # No real backing → not auto-accepted; falls through to defer.
    assert r.outcome == PromotionOutcome.DEFER


def test_outcome_explanation_is_preserved(validator):
    r = validator.validate(cand("The full suite passed 1033 tests."), [make_test_evidence()], now=NOW)
    assert r.reasons and all(isinstance(x, str) for x in r.reasons)
    assert "evidence" in r.evidence_summary
    rec = cand("The full suite passed 1033 tests.")
    r2 = validator.validate(rec, [make_test_evidence()], now=NOW)
    r2.apply(rec, now=NOW)
    assert rec.metadata["validation"]["outcome"] == "auto_accept"
    assert rec.metadata["validation"]["reasons"]


def test_apply_maps_outcome_onto_record(validator):
    rec = cand("The full suite passed 1033 tests.")
    r = validator.validate(rec, [make_test_evidence()], now=NOW)
    r.apply(rec, now=NOW)
    assert rec.status == KnowledgeStatus.ACCEPTED
    assert rec.verification == VerificationStatus.TEST_BACKED
    assert rec.is_answerable()
    assert rec.last_verified_at == NOW
    assert rec.confidence == pytest.approx(0.7)


# ---------------------------------------------------------------------------
# Pipeline integration (§8) & resilience (§9)
# ---------------------------------------------------------------------------


def test_extraction_to_validation_integration(validator):
    text = (
        "We decided to standardize on Postgres.\n\n"
        "The loader validates frontmatter before indexing.\n\n"
        "There is an auth bypass vulnerability in the token check."
    )
    extraction = KnowledgeExtractor(project="mondayos").extract(text, provider="anthropic", now=NOW)

    def evidence_for(i, record):
        if "frontmatter" in record.canonical_statement:
            return [make_test_evidence(count=5)]
        return []

    validated = validate_extraction(extraction, validator, evidence_for=evidence_for, now=NOW)
    outcomes = {vc.record.canonical_statement[:12]: vc.result.outcome for vc in validated}
    # decision → review; test-backed claim → accept; security → review
    assert outcomes["We decided t"] == PromotionOutcome.REQUIRE_HUMAN_REVIEW
    assert outcomes["The loader v"] == PromotionOutcome.AUTO_ACCEPT
    assert outcomes["There is an "] == PromotionOutcome.REQUIRE_HUMAN_REVIEW
    # status applied in place
    accepted = [vc.record for vc in validated if vc.result.outcome == PromotionOutcome.AUTO_ACCEPT]
    assert accepted[0].status == KnowledgeStatus.ACCEPTED


def test_validation_failure_never_breaks_the_path():
    class Boom(KnowledgeValidator):
        def validate(self, *a, **k):
            raise RuntimeError("simulated validation bug")

    rec = cand("The full suite passed 1033 tests.")
    r = Boom().safe_validate(rec, [make_test_evidence()], now=NOW)
    assert r.outcome == PromotionOutcome.REQUIRE_HUMAN_REVIEW
    assert "validation error" in r.reasons[0]
    # And the integration helper stays alive across a broken validator.
    extraction = ExtractionResult(candidates=[rec])
    validated = validate_extraction(extraction, Boom(), now=NOW)
    assert validated[0].result.outcome == PromotionOutcome.REQUIRE_HUMAN_REVIEW


def test_accepted_record_becomes_answerable_end_to_end(validator):
    """A validated fact is answerable; a rejected one never is."""
    good = cand("The API binds to localhost by default.")
    validator.validate(good, [make_test_evidence(count=1)], now=NOW).apply(good, now=NOW)
    assert good.is_answerable()

    bad = cand("Currently the build is red.")
    validator.validate(bad, now=NOW).apply(bad, now=NOW)
    assert not bad.is_answerable()
    assert not bad.is_usable()
