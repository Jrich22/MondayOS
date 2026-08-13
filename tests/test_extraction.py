"""Tests for §2 knowledge extraction from provider responses.

Covers the §10 cases relevant to extraction: candidate kinds are classified,
the do-not-store categories are excluded (secrets, chain-of-thought, casual,
speculation, status updates, duplicates), provenance is carried through, and
extracted records are candidates (unverified) — never auto-accepted.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from retention import (
    Durability,
    KnowledgeExtractor,
    KnowledgeKind,
    KnowledgeStatus,
    VerificationStatus,
)

NOW = datetime(2026, 7, 14, tzinfo=timezone.utc)


@pytest.fixture
def extractor() -> KnowledgeExtractor:
    return KnowledgeExtractor(project="mondayos")


def kinds(result) -> set[KnowledgeKind]:
    return {c.kind for c in result.candidates}


def statements(result) -> list[str]:
    return [c.canonical_statement for c in result.candidates]


def reasons(result) -> set[str]:
    return {d.reason for d in result.dropped}


# ---------------------------------------------------------------------------
# Kind classification
# ---------------------------------------------------------------------------


def test_decision_is_extracted(extractor):
    r = extractor.extract("We decided to use Postgres as the primary backend.", now=NOW)
    assert kinds(r) == {KnowledgeKind.DECISION}


def test_requirement_is_extracted(extractor):
    r = extractor.extract("The migration script must run before the app boots.", now=NOW)
    assert kinds(r) == {KnowledgeKind.REQUIREMENT}


def test_risk_is_extracted(extractor):
    r = extractor.extract("There is a risk that concurrent writers corrupt the sequence file.", now=NOW)
    assert kinds(r) == {KnowledgeKind.RISK}


def test_lesson_is_extracted(extractor):
    r = extractor.extract("We learned that the mutex was never released on the error path.", now=NOW)
    assert kinds(r) == {KnowledgeKind.LESSON}


def test_pattern_is_extracted(extractor):
    r = extractor.extract("By convention we always wrap store writes in a transaction.", now=NOW)
    assert kinds(r) == {KnowledgeKind.PATTERN}


def test_unresolved_question_is_extracted(extractor):
    r = extractor.extract("Open question: how do we shard knowledge across projects?", now=NOW)
    assert kinds(r) == {KnowledgeKind.QUESTION}


def test_plain_claim_defaults_to_claim(extractor):
    r = extractor.extract("The dashboard renders the brain with react-three-fiber.", now=NOW)
    assert kinds(r) == {KnowledgeKind.CLAIM}


def test_numbered_steps_become_one_runbook(extractor):
    text = "To deploy the dashboard:\n1. Build the bundle\n2. Upload to the CDN\n3. Invalidate the cache"
    r = extractor.extract(text, now=NOW)
    runbooks = [c for c in r.candidates if c.kind == KnowledgeKind.RUNBOOK]
    assert len(runbooks) == 1
    assert runbooks[0].durability == Durability.VERSION_BOUND


# ---------------------------------------------------------------------------
# Do-not-store exclusions
# ---------------------------------------------------------------------------


def test_chain_of_thought_is_stripped(extractor):
    text = "<thinking>Let me weigh the options carefully here.</thinking> We chose SQLite."
    r = extractor.extract(text, now=NOW)
    assert "chain_of_thought" in reasons(r)
    assert all("thinking" not in s.lower() for s in statements(r))
    assert any(c.kind == KnowledgeKind.DECISION for c in r.candidates)


def test_reasoning_preamble_line_is_stripped(extractor):
    text = "Reasoning: the user probably wants durability.\nWe decided to use Postgres."
    r = extractor.extract(text, now=NOW)
    assert "chain_of_thought" in reasons(r)
    assert kinds(r) == {KnowledgeKind.DECISION}


@pytest.mark.parametrize(
    "secret",
    [
        "The key is sk-ant-abcd1234efgh5678ijkl for anthropic.",
        "Use api_key=SUPERSECRETVALUE123 to authenticate.",
        "Authorization: Bearer abcdef0123456789ghijkl was set.",
        "AWS key AKIAIOSFODNN7EXAMPLE is configured.",
    ],
)
def test_secrets_are_never_stored(extractor, secret):
    r = extractor.extract(secret, now=NOW)
    assert r.candidates == []
    assert "secret" in reasons(r)
    # The secret text itself must not survive in the dropped audit.
    assert all("sk-ant" not in d.text and "AKIA" not in d.text for d in r.dropped)


def test_casual_conversation_is_dropped(extractor):
    r = extractor.extract("Sure, happy to help! Let me know if you need anything else.", now=NOW)
    assert r.candidates == []
    assert "casual" in reasons(r)


def test_short_announcement_is_dropped_but_root_cause_kept(extractor):
    r = extractor.extract(
        "Here is my recommendation. Here is the root cause: the mutex was never released.",
        now=NOW,
    )
    kept = statements(r)
    assert not any("recommendation" in s for s in kept)
    assert any("root cause" in s for s in kept)


def test_unsupported_speculation_is_dropped(extractor):
    r = extractor.extract("Maybe we could try DynamoDB later, not sure.", now=NOW)
    assert r.candidates == []
    assert "speculation" in reasons(r)


def test_speculative_question_is_still_kept(extractor):
    # A gap is knowledge — hedging must not suppress an unresolved question.
    r = extractor.extract("I'm not sure — how should we handle multi-project sharding?", now=NOW)
    assert kinds(r) == {KnowledgeKind.QUESTION}


def test_transient_status_update_is_dropped(extractor):
    r = extractor.extract("Currently the CI pipeline is running slowly.", now=NOW)
    assert r.candidates == []
    assert "status_update" in reasons(r)


def test_duplicate_statements_are_collapsed(extractor):
    text = "We chose Postgres for durability. We chose Postgres for durability."
    r = extractor.extract(text, now=NOW)
    assert len(r.candidates) == 1
    assert "duplicate" in reasons(r)


def test_too_short_segment_is_dropped(extractor):
    r = extractor.extract("Yes. No. Done.", now=NOW)
    assert r.candidates == []
    assert "too_short" in reasons(r)


# ---------------------------------------------------------------------------
# Provenance & candidate defaults
# ---------------------------------------------------------------------------


def test_provenance_is_carried_through(extractor):
    r = extractor.extract(
        "We decided to use Postgres.",
        provider="anthropic", model="claude-opus-4-8", run="wf-101", task="TASK-0042",
        now=NOW,
    )
    c = r.candidates[0]
    assert c.source_provider == "anthropic"
    assert c.source_model == "claude-opus-4-8"
    assert c.source_run == "wf-101"
    assert c.source_task == "TASK-0042"


def test_extracted_records_are_unverified_candidates(extractor):
    r = extractor.extract("We decided to use Postgres.", now=NOW)
    c = r.candidates[0]
    assert c.status == KnowledgeStatus.CANDIDATE
    assert c.verification == VerificationStatus.UNVERIFIED
    assert c.knowledge_id == ""  # unassigned until persisted


def test_volatile_claim_is_time_sensitive(extractor):
    r = extractor.extract("Opus pricing is $15 per million output tokens.", now=NOW)
    c = r.candidates[0]
    assert c.durability == Durability.TIME_SENSITIVE


def test_project_scope_is_applied(extractor):
    r = extractor.extract("We decided to use Postgres.", project="cue-app", now=NOW)
    assert r.candidates[0].project == "cue-app"


def test_extract_from_response_reads_duck_typed_object(extractor):
    class Resp:
        content = "We decided to use Postgres."
        provider = "openai"
        model = "gpt-4o"

    r = extractor.extract_from_response(Resp(), run="wf-9", now=NOW)
    assert r.candidates[0].source_provider == "openai"
    assert r.candidates[0].source_model == "gpt-4o"


def test_empty_response_yields_nothing(extractor):
    r = extractor.extract("", now=NOW)
    assert r.candidates == [] and r.dropped == []


def test_end_to_end_mixed_response(extractor):
    """A realistic messy response: keep 5 durable kinds, drop 4 categories."""
    text = (
        "<thinking>weighing options</thinking>\n"
        "Sure, happy to help!\n\n"
        "We decided to standardize on Postgres for the knowledge backend.\n\n"
        "The loader must validate frontmatter before indexing.\n\n"
        "There is a risk that a corrupt sequence file blocks all writes.\n\n"
        "By convention, every store write goes through a transaction.\n\n"
        "Open question: do we need per-project sharding?\n\n"
        "Maybe we switch to Neo4j someday, not sure.\n\n"
        "The token is sk-ant-verysecretlongtokenvalue1234."
    )
    r = extractor.extract(text, provider="anthropic", now=NOW)
    assert kinds(r) == {
        KnowledgeKind.DECISION,
        KnowledgeKind.REQUIREMENT,
        KnowledgeKind.RISK,
        KnowledgeKind.PATTERN,
        KnowledgeKind.QUESTION,
    }
    assert {"chain_of_thought", "casual", "speculation", "secret"} <= reasons(r)
