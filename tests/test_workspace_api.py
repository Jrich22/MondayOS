"""
Provenance of AI Workspace knowledge capture, through the public Monday API.

Separate from tests/test_workspace.py because the behaviour under test lives in
``Monday.workspace()`` and the capture closure in ``monday/api.py`` — the
workspace package itself only calls an injected callable. Keeping these here
means the domain layer and the API layer each stay independently testable.

The subject is narrow: a captured entry must tell the truth about how it came to
exist. Two parties did two different things — a model produced the text, a human
decided to keep it — and an entry that records only one of them misattributes.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_workspace import FakeProvider, _project_tree  # noqa: E402


class TestKnowledgeCaptureProvenance(unittest.TestCase):
    """
    The captured entry must tell the truth about how it came to exist.

    Two parties are involved and they did different things: a model produced the
    text, a human decided to keep it. An entry that records only one of them
    misattributes, and an entry filed under a type implying investigation claims
    a rigour that never happened.
    """

    def _capture(self, tmp: str):
        from monday import Monday, MondayConfig

        root = Path(tmp)
        (root / "config").mkdir(parents=True, exist_ok=True)
        project = _project_tree(root, "alpha")

        monday = Monday(MondayConfig(project_root=root))
        monday.project("register", name="alpha", path=str(project))
        monday._Monday__provider = FakeProvider("A durable reference answer.")  # type: ignore[attr-defined]

        conversation = monday.workspace("create-conversation", project="alpha").data
        sent = monday.workspace(
            "send-message", project="alpha", conversation_id=conversation["id"], content="q"
        )
        message_id = sent.data["assistant_message"]["id"]
        captured = monday.workspace(
            "save-to-knowledge",
            project="alpha",
            conversation_id=conversation["id"],
            message_id=message_id,
        )
        entry = monday._Monday__knowledge.get(captured.data["knowledge_id"])  # type: ignore[attr-defined]
        return entry, conversation["id"], message_id, sent.data["context"]["id"]

    def test_the_type_does_not_claim_an_investigation_happened(self):
        """
        MKS RESEARCH mandates a question, methodology and findings (9.10).

        A model answering in a chat did none of those. DOCUMENTATION (9.9) is a
        structured reference record, which is what this actually is.
        """
        with TemporaryDirectory() as tmp:
            entry, _, _, _ = self._capture(tmp)
            self.assertEqual(entry.entry_type.value, "documentation")
            self.assertTrue(entry.id.startswith("DOC-"))

    def test_the_mandatory_type_fields_for_documentation_are_supplied(self):
        with TemporaryDirectory() as tmp:
            entry, conversation_id, _, _ = self._capture(tmp)
            self.assertEqual(entry.type_fields["content_type"], "REFERENCE")
            self.assertIn(conversation_id, entry.type_fields["scope"])

    def test_the_model_is_recorded_as_author_and_the_human_as_saver(self):
        with TemporaryDirectory() as tmp:
            entry, _, _, _ = self._capture(tmp)
            self.assertEqual(entry.authored_by, "agent")
            self.assertEqual(entry.metadata["saved_by"], "human")
            self.assertIn("fake", entry.metadata["produced_by"])

    def test_provenance_links_back_to_project_conversation_and_message(self):
        with TemporaryDirectory() as tmp:
            entry, conversation_id, message_id, snapshot_id = self._capture(tmp)
            self.assertEqual(entry.metadata["project"], "alpha")
            self.assertEqual(entry.metadata["conversation_id"], conversation_id)
            self.assertEqual(entry.metadata["message_id"], message_id)
            self.assertEqual(entry.metadata["context_snapshot_id"], snapshot_id)
            self.assertEqual(entry.metadata["source"], "ai-workspace")
            self.assertTrue(entry.metadata["saved_at"])

    def test_provider_and_model_are_recorded_where_available(self):
        with TemporaryDirectory() as tmp:
            entry, _, _, _ = self._capture(tmp)
            self.assertEqual(entry.metadata["provider"], "fake")
            self.assertEqual(entry.metadata["model"], "fake-1")

    def test_unverified_content_does_not_claim_full_confidence(self):
        """A human electing to keep an answer is not a human checking it."""
        with TemporaryDirectory() as tmp:
            entry, _, _, _ = self._capture(tmp)
            self.assertLess(entry.confidence, 1.0)
            self.assertIn("none", entry.metadata["verification"])

    def test_the_body_states_that_nothing_verified_it(self):
        with TemporaryDirectory() as tmp:
            entry, _, _, _ = self._capture(tmp)
            self.assertIn("Not independently verified", entry.body)
