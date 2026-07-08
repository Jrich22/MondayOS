"""
Regression tests for structured agent verdicts (MondayOS v2.4).

These lock in the fix for the false security veto: pass/block is decided by a
structured verdict field, never by substring matching over natural-language
output. The four headline regressions from the spec:

    - "blocker" in prose does NOT block
    - "blocking issue" in prose does NOT block
    - verdict=block DOES block
    - verdict=pass always passes

covered both at the parser level (parse_verdict) and end-to-end through the team
pipeline.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from agents.adapters import FakeAgentProvider
from agents.verdicts import (
    BLOCK,
    NEEDS_CHANGES,
    PASS,
    AgentVerdict,
    normalize_verdict,
    parse_verdict,
)
from brain.providers.base import ProviderResponse
from monday import Monday, MondayConfig


# ---------------------------------------------------------------------------
# parse_verdict — prose never blocks
# ---------------------------------------------------------------------------

class TestProseNeverBlocks(unittest.TestCase):
    def test_word_blocker_in_prose_does_not_block(self):
        text = (
            "As the security agent I reviewed the change. Earlier there was a "
            "blocker in the auth flow, but it has since been resolved and the "
            "code looks good to me."
        )
        v = parse_verdict(text, role="security")
        self.assertEqual(v.verdict, PASS)
        self.assertFalse(v.blocks)

    def test_phrase_blocking_issue_in_prose_does_not_block(self):
        text = (
            "QA notes: I checked for any blocking issue and a critical blocker "
            "regression. None reproduce. I recommend we proceed."
        )
        v = parse_verdict(text, role="qa")
        self.assertEqual(v.verdict, PASS)

    def test_words_reject_veto_in_prose_do_not_block(self):
        # The old marker list included REJECT/VETO — plain prose use must not veto.
        text = "I would not reject this; there is no reason to veto the work."
        self.assertEqual(parse_verdict(text, role="reviewer").verdict, PASS)

    def test_empty_response_is_pass_empty_source(self):
        v = parse_verdict("", role="qa")
        self.assertEqual(v.verdict, PASS)
        self.assertEqual(v.source, "empty")


# ---------------------------------------------------------------------------
# parse_verdict — structured verdicts are honoured
# ---------------------------------------------------------------------------

class TestStructuredVerdicts(unittest.TestCase):
    def test_json_block_does_block(self):
        text = (
            "Here is my review. There is a serious problem.\n\n"
            '```json\n{"verdict": "block", "confidence": "high", '
            '"summary": "SQL injection in query builder", '
            '"findings": ["unsanitised input"], "recommendations": ["parameterise"]}\n```'
        )
        v = parse_verdict(text, role="security")
        self.assertEqual(v.verdict, BLOCK)
        self.assertTrue(v.blocks)
        self.assertEqual(v.source, "json")
        self.assertEqual(v.confidence, "high")
        self.assertEqual(v.summary, "SQL injection in query builder")
        self.assertEqual(v.findings, ["unsanitised input"])
        self.assertEqual(v.recommendations, ["parameterise"])

    def test_json_pass_always_passes(self):
        text = '```json\n{"verdict": "pass", "confidence": "high", "summary": "LGTM"}\n```'
        v = parse_verdict(text, role="reviewer")
        self.assertEqual(v.verdict, PASS)
        self.assertEqual(v.source, "json")

    def test_bare_json_without_fence(self):
        text = 'All done. {"verdict": "pass", "summary": "ok"} thanks'
        self.assertEqual(parse_verdict(text).verdict, PASS)

    def test_json_needs_changes(self):
        text = '{"verdict": "needs_changes", "summary": "add a test"}'
        self.assertEqual(parse_verdict(text).verdict, NEEDS_CHANGES)

    def test_json_block_wins_even_with_reassuring_prose(self):
        text = (
            "Everything looks great and I am happy to approve, nice work!\n"
            '```json\n{"verdict": "block", "summary": "secret committed"}\n```'
        )
        self.assertEqual(parse_verdict(text).verdict, BLOCK)

    def test_labelled_verdict_without_json_blocks(self):
        # An explicit labelled declaration (no JSON) is honoured.
        self.assertEqual(parse_verdict("Verdict: block\nreason: leak").verdict, BLOCK)

    def test_labelled_verdict_blocker_token_still_passes(self):
        # "verdict: blocker" is malformed — normalise conservatively to pass,
        # and crucially never let the "block" substring inside "blocker" veto.
        self.assertEqual(parse_verdict("verdict: blocker things remain").verdict, PASS)

    def test_findings_from_list_of_objects(self):
        text = '{"verdict": "needs_changes", "findings": [{"title": "flaky test"}, {"description": "slow"}]}'
        v = parse_verdict(text)
        self.assertEqual(v.findings, ["flaky test", "slow"])

    def test_confidence_number_coerced_to_string(self):
        text = '{"verdict": "pass", "confidence": 0.92}'
        self.assertEqual(parse_verdict(text).confidence, "0.92")


class TestNormalizeVerdict(unittest.TestCase):
    def test_synonyms(self):
        for word in ("block", "blocked", "reject", "veto", "fail", "DO_NOT_MERGE"):
            self.assertEqual(normalize_verdict(word), BLOCK, word)
        for word in ("pass", "approved", "lgtm", "ok"):
            self.assertEqual(normalize_verdict(word), PASS, word)
        for word in ("needs_changes", "needs-changes", "revise", "rework"):
            self.assertEqual(normalize_verdict(word), NEEDS_CHANGES, word)

    def test_unknown_defaults_pass(self):
        self.assertEqual(normalize_verdict("blocker"), PASS)
        self.assertEqual(normalize_verdict(""), PASS)
        self.assertEqual(normalize_verdict(None), PASS)


# ---------------------------------------------------------------------------
# End-to-end: prose blocker does not stop the team; structured block does
# ---------------------------------------------------------------------------

class _ProseOnlyProvider(FakeAgentProvider):
    """A provider that emits ONLY natural language — no structured JSON — with
    the words 'blocker' and 'blocking issue' in it. Proves prose never vetoes."""

    def _prose(self, subject: str) -> str:
        return (
            f"[{self.name}] Acting as the {self._role} agent, I reviewed the work. "
            "I initially worried about a blocker and a blocking issue in the flow, "
            "but on inspection everything is fine. This is ready for human review. "
            f"Objective: {subject.strip()[:160]}"
        )

    def ask(self, prompt: str, context: str = "", max_tokens: int = 1024, **kwargs: Any) -> ProviderResponse:
        self.calls.append(("ask", prompt))
        return ProviderResponse(content=self._prose(prompt), model=f"{self.name}-1", provider=self.name, tokens_used=7)


class TestTeamPipelineStructured(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.monday = Monday(MondayConfig(project_root=self.root))
        self.task_id = self.monday.task(
            "create", title="Build the app shell",
            objective="Stand up the app shell.", task_type="feature", priority="P1",
        ).task_id

    def tearDown(self):
        self._tmp.cleanup()

    def test_prose_blocker_from_security_does_not_block(self):
        # Every stage emits prose containing "blocker"/"blocking issue" and NO JSON.
        provs = {role: _ProseOnlyProvider(role=role) for role in
                 ("cpo", "lead-engineer", "qa", "security", "reviewer")}
        r = self.monday.team("run", task_id=self.task_id, stage_providers=provs)
        self.assertTrue(r.success, r.message)
        self.assertEqual(r.status, "awaiting-approval")
        self.assertTrue(all(s["verdict"] == "pass" for s in r.stages))

    def test_structured_block_from_security_stops(self):
        provs = {"security": FakeAgentProvider(role="security", verdict="block")}
        r = self.monday.team("run", task_id=self.task_id, provider="fake", stage_providers=provs)
        self.assertFalse(r.success)
        self.assertEqual(r.status, "blocked")
        self.assertEqual(r.stopped_at, "security")

    def test_structured_needs_changes_marks_changes_requested(self):
        provs = {"qa": FakeAgentProvider(role="qa", verdict="needs_changes")}
        r = self.monday.team("run", task_id=self.task_id, provider="fake", stage_providers=provs)
        self.assertFalse(r.success)
        self.assertEqual(r.status, "changes-requested")
        self.assertEqual(r.stopped_at, "qa")

    def test_runs_persist_structured_verdict(self):
        r = self.monday.team("run", task_id=self.task_id, provider="fake")
        # Each child run JSON carries a structured verdict block.
        for run_id in r.data["child_run_ids"]:
            data = json.loads((self.root / "logs" / "agents" / f"{run_id}.json").read_text())
            self.assertIn("verdict", data)
            self.assertEqual(data["verdict"]["verdict"], "pass")
            self.assertEqual(data["verdict"]["source"], "json")
            # The full original response is preserved for humans.
            self.assertIn("result_full", data["execution"])


if __name__ == "__main__":
    unittest.main()
