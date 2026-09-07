"""
Tests for persisted strategic state.

The rule this type exists to enforce is negative: **every field was visible in a
completed answer**. No prompts, no rendered assessment text, no model reasoning,
no candidates that lost before being shown. If a reader could not have seen it, it
is not continuity state — it is hidden reasoning wearing a struct. Several tests
here do nothing but check that nothing else got in.

The second property is that state is a *record*, not a cache. It survives a
restart because it lives in the conversation file that already survives one, and
it cannot describe another project because it lives inside a project-scoped file.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from workspace.models import (
    AlternativeRef,
    EvidenceRef,
    Score,
    StrategicState,
    recommendation_key,
)
from workspace.store import ConversationStore

NOW = datetime(2026, 9, 7, 10, 0, 0, tzinfo=UTC)


def _state(project: str = "mondayos") -> StrategicState:
    return StrategicState(
        question="What should we build next?",
        topic="next-work",
        source_message_id="MSG-0002",
        snapshot_id="SNAP-0007",
        fingerprint="abc123def456",
        project=project,
        created_at=NOW,
        recommendation_key=recommendation_key("Add a test suite for integrations", "integrations"),
        recommendation="Add a test suite for integrations",
        rationale="integrations is at-risk: implementation with no tests",
        initiative_slug="integrations",
        effort="scoped to one capability",
        alternatives=[
            AlternativeRef("Address workspace under-testing", "integrations unblocks more first"),
            AlternativeRef("Write a testing strategy", "narrower payoff this week"),
        ],
        evidence_refs=[
            EvidenceRef("file", "integrations/", "integrations/", 0, "11 source files, no tests"),
            EvidenceRef("decision", "ADR-019", "docs/DECISIONS.md", 412, "decides this area"),
        ],
        initiative_slugs=["integrations", "workspace"],
        evidence_strength=Score(0.94, "high"),
        confidence=Score(0.47, "medium"),
        execution_risk=Score(0.43, "moderate"),
    )


class TestRecommendationKey(unittest.TestCase):
    def test_the_key_is_derived_not_allocated(self):
        """
        Two identical recommendations about the same capability are the same
        recommendation, even computed in different conversations. A counter would
        give them different ids, which is exactly backwards.
        """
        first = recommendation_key("Add a test suite for integrations", "integrations")
        second = recommendation_key("Add a test suite for integrations", "integrations")
        self.assertEqual(first, second)

    def test_rewording_whitespace_or_case_does_not_change_it(self):
        """Matching on raw statement text would treat a reflow as a new decision."""
        self.assertEqual(
            recommendation_key("Add a test suite for integrations", "integrations"),
            recommendation_key("  add   a Test Suite  for INTEGRATIONS ", "integrations"),
        )

    def test_a_different_capability_is_a_different_recommendation(self):
        self.assertNotEqual(
            recommendation_key("Add a test suite", "integrations"),
            recommendation_key("Add a test suite", "workspace"),
        )

    def test_it_is_bounded_and_stable(self):
        self.assertEqual(len(recommendation_key("x", "y")), 16)


class TestSerialisation(unittest.TestCase):
    def test_round_trip_preserves_every_field(self):
        original = _state()
        restored = StrategicState.from_dict(original.to_dict())
        self.assertEqual(restored.to_dict(), original.to_dict())

    def test_the_recommendation_key_survives_a_round_trip(self):
        original = _state()
        restored = StrategicState.from_dict(original.to_dict())
        self.assertEqual(restored.recommendation_key, original.recommendation_key)
        self.assertEqual(
            restored.recommendation_key,
            recommendation_key(original.recommendation, original.initiative_slug),
        )

    def test_alternatives_keep_the_order_they_were_shown_in(self):
        """
        "The second option" is only answerable if storage preserves what was on
        screen. Nothing here re-sorts.
        """
        restored = StrategicState.from_dict(_state().to_dict())
        self.assertEqual(restored.alternative_at(1).statement, "Address workspace under-testing")
        self.assertEqual(restored.alternative_at(2).statement, "Write a testing strategy")
        self.assertIsNone(restored.alternative_at(3))
        self.assertIsNone(restored.alternative_at(0))

    def test_state_is_bounded(self):
        """A continuity record, not a second transcript."""
        raw = _state().to_dict()
        raw["alternatives"] = [{"statement": f"a{i}", "why_not": "x"} for i in range(50)]
        raw["evidence_refs"] = [{"kind": "file", "reference": f"f{i}"} for i in range(50)]
        raw["initiative_slugs"] = [f"i{i}" for i in range(50)]
        restored = StrategicState.from_dict(raw)
        self.assertLessEqual(len(restored.alternatives), 4)
        self.assertLessEqual(len(restored.evidence_refs), 12)
        self.assertLessEqual(len(restored.initiative_slugs), 8)

    def test_a_malformed_block_yields_no_state_rather_than_raising(self):
        self.assertTrue(StrategicState.from_dict({}).empty)
        self.assertTrue(StrategicState.from_dict({"junk": 1}).empty)

    def test_an_absent_timestamp_is_absent_not_the_epoch(self):
        """ "Recorded in 1970" is a different claim from "not recorded"."""
        self.assertIsNone(StrategicState.from_dict({"recommendation": "x"}).created_at)

    def test_render_reads_as_a_record_not_a_fresh_conclusion(self):
        text = _state().render()
        self.assertIn("Prior recommendation", text)
        self.assertIn("already given to the user", text)
        self.assertIn("Add a test suite for integrations", text)
        self.assertIn("Alternatives shown, in the order presented", text)
        self.assertIn("evidence strength high", text)


class TestConversationPersistence(unittest.TestCase):
    def test_state_survives_a_restart(self):
        """
        Restart-safety comes free: state lives in the conversation file, which
        already survives one. That is the whole reason not to build a second store.
        """
        with TemporaryDirectory() as tmp:
            store = ConversationStore(Path(tmp))
            conversation = store.create("mondayos", "Strategy")
            conversation.strategy = _state()
            store.save(conversation)

            # A genuinely new store object, as a restarted process would build.
            reloaded = ConversationStore(Path(tmp)).get("mondayos", conversation.id)
            self.assertIsNotNone(reloaded.strategy)
            self.assertEqual(reloaded.strategy.recommendation, "Add a test suite for integrations")
            self.assertEqual(reloaded.strategy.initiative_slug, "integrations")
            self.assertEqual(reloaded.strategy.confidence.band, "medium")
            self.assertEqual(len(reloaded.strategy.alternatives), 2)

    def test_a_conversation_without_strategy_stays_unchanged(self):
        """An ordinary conversation file gains nothing."""
        with TemporaryDirectory() as tmp:
            store = ConversationStore(Path(tmp))
            conversation = store.create("mondayos", "Ordinary")
            path = store.save(conversation)
            self.assertNotIn("strategy:", path.read_text())
            self.assertIsNone(store.get("mondayos", conversation.id).strategy)

    def test_strategic_state_cannot_describe_another_project(self):
        """
        Defence in depth. State already lives inside a project-scoped file, so a
        mismatch means the file was hand-edited or moved -- and a recommendation
        attributed to the wrong project is worse than having none.
        """
        with TemporaryDirectory() as tmp:
            store = ConversationStore(Path(tmp))
            conversation = store.create("mondayos", "Strategy")
            conversation.strategy = _state(project="cue-app")
            store.save(conversation)
            self.assertIsNone(store.get("mondayos", conversation.id).strategy)

    def test_state_is_replaced_wholesale_never_appended(self):
        with TemporaryDirectory() as tmp:
            store = ConversationStore(Path(tmp))
            conversation = store.create("mondayos", "Strategy")
            conversation.strategy = _state()
            store.save(conversation)

            replacement = _state()
            replacement.recommendation = "Ship the Billing spike"
            replacement.initiative_slug = "billing"
            conversation.strategy = replacement
            store.save(conversation)

            reloaded = store.get("mondayos", conversation.id)
            self.assertEqual(reloaded.strategy.recommendation, "Ship the Billing spike")
            # One state, not a history of them.
            self.assertNotIn("Add a test suite for integrations", str(reloaded.strategy.to_dict()))

    def test_no_prompt_or_hidden_reasoning_is_persisted(self):
        """
        The load-bearing negative test.

        Anything a reader could not have seen must not be in the file. Checked
        against the real serialised bytes rather than against the dataclass, so a
        field added later without thought is caught here.
        """
        with TemporaryDirectory() as tmp:
            store = ConversationStore(Path(tmp))
            conversation = store.create("mondayos", "Strategy")
            conversation.strategy = _state()
            raw = store.save(conversation).read_text().lower()

            for forbidden in (
                "you are mondayos",
                "system_instruction",
                "executive_instruction",
                "chain of thought",
                "chain_of_thought",
                "scratchpad",
                "thinking",
                "reasoning_trace",
                "prompt",
                "context snapshot",
                "candidate",
                "leverage",
            ):
                self.assertNotIn(forbidden, raw, f"{forbidden!r} leaked into the conversation file")

    def test_the_persisted_keys_are_exactly_the_agreed_set(self):
        """A new field cannot appear without this test being updated deliberately."""
        self.assertEqual(
            sorted(_state().to_dict()),
            sorted(
                [
                    "question",
                    "topic",
                    "source_message_id",
                    "snapshot_id",
                    "fingerprint",
                    "project",
                    "created_at",
                    "recommendation_key",
                    "recommendation",
                    "rationale",
                    "initiative_slug",
                    "effort",
                    "alternatives",
                    "evidence_refs",
                    "initiative_slugs",
                    "evidence_strength",
                    "confidence",
                    "execution_risk",
                ]
            ),
        )


if __name__ == "__main__":
    unittest.main()
