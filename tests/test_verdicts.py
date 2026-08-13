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
    INVALID,
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
    """
    Prose never decides the verdict — in EITHER direction.

    These originally asserted that blocker-ish prose yields `pass`. The
    no-false-veto guarantee is unchanged and still asserted (`blocks` is False),
    but prose alone can no longer produce a pass either: with no structured
    verdict the outcome is `invalid`. Prose is not a signal, full stop.
    """

    def test_word_blocker_in_prose_does_not_block(self):
        text = (
            "As the security agent I reviewed the change. Earlier there was a "
            "blocker in the auth flow, but it has since been resolved and the "
            "code looks good to me."
        )
        v = parse_verdict(text, role="security")
        self.assertFalse(v.blocks)
        self.assertEqual(v.verdict, INVALID)

    def test_phrase_blocking_issue_in_prose_does_not_block(self):
        text = (
            "QA notes: I checked for any blocking issue and a critical blocker "
            "regression. None reproduce. I recommend we proceed."
        )
        v = parse_verdict(text, role="qa")
        self.assertFalse(v.blocks)
        self.assertEqual(v.verdict, INVALID)

    def test_words_reject_veto_in_prose_do_not_block(self):
        # The old marker list included REJECT/VETO — plain prose use must not veto.
        text = "I would not reject this; there is no reason to veto the work."
        v = parse_verdict(text, role="reviewer")
        self.assertFalse(v.blocks)
        self.assertEqual(v.verdict, INVALID)

    def test_empty_response_is_invalid_not_pass(self):
        v = parse_verdict("", role="qa")
        self.assertEqual(v.verdict, INVALID)
        self.assertEqual(v.source, "empty")
        self.assertFalse(v.is_valid)
        self.assertIn("empty", v.reason)


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
        # A labelled STOP signal is honoured from any source — conservative.
        self.assertEqual(parse_verdict("Verdict: block\nreason: leak").verdict, BLOCK)

    def test_labelled_needs_changes_without_json_is_honoured(self):
        self.assertEqual(parse_verdict("verdict: needs_changes").verdict, NEEDS_CHANGES)

    def test_labelled_pass_without_json_is_invalid(self):
        # A labelled pass is NOT a structured verdict. Prose cannot grant a pass.
        v = parse_verdict("Verdict: pass — everything is fine")
        self.assertEqual(v.verdict, INVALID)
        self.assertIn("prose cannot grant a pass", v.reason)

    def test_labelled_verdict_blocker_token_is_invalid_not_pass(self):
        # "verdict: blocker" is malformed. It must not veto (the "block"
        # substring inside "blocker" is not a veto), and must not pass either.
        v = parse_verdict("verdict: blocker things remain")
        self.assertFalse(v.blocks)
        self.assertEqual(v.verdict, INVALID)

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

    def test_unknown_is_invalid_never_pass(self):
        # The headline defect: an unrecognised token used to normalise to PASS.
        self.assertEqual(normalize_verdict("blocker"), INVALID)
        self.assertEqual(normalize_verdict(""), INVALID)
        self.assertEqual(normalize_verdict(None), INVALID)
        self.assertEqual(normalize_verdict("maybe"), INVALID)


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

    def test_prose_blocker_does_not_veto_but_does_not_pass_either(self):
        """
        Prose-only output stops the run — for the right reason.

        This previously asserted the run reached `awaiting-approval`: prose
        containing "blocker" did not veto, so every stage defaulted to pass and
        the pipeline sailed through on five stages that never produced a verdict.
        The no-false-veto guarantee is preserved (status is not `blocked`), but
        the run now stops at the first blocking role for lack of a verdict.
        """
        provs = {role: _ProseOnlyProvider(role=role) for role in
                 ("cpo", "lead-engineer", "qa", "security", "reviewer")}
        r = self.monday.team("run", task_id=self.task_id, stage_providers=provs)
        self.assertFalse(r.success)
        self.assertEqual(r.status, "invalid-verdict")   # not "blocked" — no false veto
        self.assertEqual(r.stopped_at, "qa")            # first blocking role

    def test_non_blocking_roles_still_advance_without_a_verdict(self):
        """CPO and Lead Engineer are productive roles, not gatekeepers."""
        provs = {
            "cpo": _ProseOnlyProvider(role="cpo"),
            "lead-engineer": _ProseOnlyProvider(role="lead-engineer"),
        }
        r = self.monday.team("run", task_id=self.task_id, provider="fake",
                             stage_providers=provs)
        self.assertTrue(r.success, r.message)
        self.assertEqual(r.status, "awaiting-approval")
        by_role = {s["role"]: s for s in r.stages}
        # Their true verdict is recorded honestly for audit...
        self.assertEqual(by_role["cpo"]["verdict"], "invalid")
        # ...but it did not stop the run, and the blocking roles did pass.
        self.assertEqual(by_role["qa"]["verdict"], "pass")
        self.assertEqual(by_role["reviewer"]["verdict"], "pass")

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


# ---------------------------------------------------------------------------
# TASK-0055 — a missing/malformed/truncated verdict must never mean pass
# ---------------------------------------------------------------------------

_REASSURING_PROSE = (
    "I completed a full review of the work. Everything looks good to me. "
    "Checkpoint 1: PASS. Checkpoint 2: PASS. Checkpoint 7: PASS. "
    "There is no blocker here and no reason to reject. LGTM — approved."
)


class TestNoVerdictNeverPasses(unittest.TestCase):
    """
    The defect this task fixes: an absent verdict used to default to `pass`.

    Every input below is one a human might skim and read as approval. None may
    be accepted as one.
    """

    def test_missing_json_is_invalid(self):
        v = parse_verdict(_REASSURING_PROSE, role="reviewer")
        self.assertEqual(v.verdict, INVALID)
        self.assertFalse(v.passed)
        self.assertFalse(v.is_valid)
        self.assertIn("no structured JSON verdict", v.reason)

    def test_malformed_json_is_invalid(self):
        text = _REASSURING_PROSE + '\n```json\n{"verdict": "pass", "summary": oops,,}\n```'
        v = parse_verdict(text, role="qa")
        self.assertEqual(v.verdict, INVALID)
        self.assertFalse(v.passed)

    def test_truncated_json_is_invalid(self):
        # Cut off mid-object, exactly as an output-token ceiling produces.
        text = _REASSURING_PROSE + '\n```json\n{\n  "verdict": "pass",\n  "confidence": "hi'
        v = parse_verdict(text, role="security", truncated=True)
        self.assertEqual(v.verdict, INVALID)
        self.assertIn("truncated", v.reason)

    def test_truncated_flag_is_reported_even_without_json(self):
        v = parse_verdict("A long review that ran out of room", truncated=True)
        self.assertEqual(v.verdict, INVALID)
        self.assertIn("truncated", v.reason)

    def test_truncated_but_verdict_already_emitted_is_honoured(self):
        """
        The point of asking for the JSON first: prose gets cut, verdict survives.
        """
        text = (
            '```json\n{"verdict": "needs_changes", "summary": "add a test"}\n```\n'
            "Now let me explain at length why, and then I got cut off mid-sent"
        )
        v = parse_verdict(text, role="reviewer", truncated=True)
        self.assertEqual(v.verdict, NEEDS_CHANGES)
        self.assertEqual(v.source, "json")

    def test_unrecognised_verdict_value_is_invalid(self):
        v = parse_verdict('{"verdict": "probably fine", "summary": "eh"}')
        self.assertEqual(v.verdict, INVALID)
        self.assertIn("unrecognised", v.reason)

    def test_null_verdict_value_is_invalid(self):
        self.assertEqual(parse_verdict('{"verdict": null}').verdict, INVALID)

    def test_prose_saying_PASS_without_structure_is_invalid(self):
        v = parse_verdict("Checkpoint 7: PASS. Overall: PASS. Ship it.", role="qa")
        self.assertEqual(v.verdict, INVALID)

    def test_prose_saying_BLOCK_but_structured_pass_yields_pass(self):
        """Structure wins over prose in both directions."""
        text = (
            "This is a BLOCK. I am vetoing. Do not merge.\n"
            '```json\n{"verdict": "pass", "summary": "actually fine"}\n```'
        )
        self.assertEqual(parse_verdict(text).verdict, PASS)

    def test_long_response_with_leading_verdict_stays_parseable(self):
        text = (
            '```json\n{"verdict": "pass", "confidence": "high", "summary": "sound"}\n```\n'
            + ("Detailed prose paragraph explaining the reasoning. " * 800)
        )
        v = parse_verdict(text, role="reviewer")
        self.assertEqual(v.verdict, PASS)
        self.assertEqual(v.source, "json")

    def test_default_constructed_verdict_is_invalid(self):
        self.assertEqual(AgentVerdict().verdict, INVALID)
        self.assertFalse(AgentVerdict().passed)


class TestBlockingRolesRequireValidVerdict(unittest.TestCase):
    """Each blocking role must produce a valid pass, or the pipeline stops."""

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

    def _run(self, role: str, mode: str):
        provs = {role: FakeAgentProvider(role=role, verdict=mode)}
        return self.monday.team("run", task_id=self.task_id, provider="fake",
                                stage_providers=provs)

    def test_qa_invalid_verdict_stops_pipeline(self):
        for mode in ("no_verdict", "malformed", "truncated"):
            with self.subTest(mode=mode):
                r = self._run("qa", mode)
                self.assertFalse(r.success)
                self.assertEqual(r.status, "invalid-verdict")
                self.assertEqual(r.stopped_at, "qa")

    def test_security_invalid_verdict_stops_pipeline(self):
        for mode in ("no_verdict", "malformed", "truncated"):
            with self.subTest(mode=mode):
                r = self._run("security", mode)
                self.assertFalse(r.success)
                self.assertEqual(r.status, "invalid-verdict")
                self.assertEqual(r.stopped_at, "security")

    def test_reviewer_invalid_verdict_stops_pipeline(self):
        for mode in ("no_verdict", "malformed", "truncated"):
            with self.subTest(mode=mode):
                r = self._run("reviewer", mode)
                self.assertFalse(r.success)
                self.assertEqual(r.status, "invalid-verdict")
                self.assertEqual(r.stopped_at, "reviewer")

    def test_stop_reason_explains_why(self):
        r = self._run("security", "no_verdict")
        self.assertIn("no valid structured verdict", r.data["stopped_reason"])

    def test_invalid_verdict_is_not_reported_as_a_veto(self):
        # It stops the run, but it is not a `block` — the distinction matters
        # for humans triaging why a run halted.
        r = self._run("qa", "no_verdict")
        self.assertNotEqual(r.status, "blocked")

    def test_full_valid_pass_still_requires_human_approval(self):
        """The ApprovalGate is preserved: all-pass reaches REVIEW, not done."""
        r = self.monday.team("run", task_id=self.task_id, provider="fake")
        self.assertTrue(r.success, r.message)
        self.assertEqual(r.status, "awaiting-approval")
        self.assertTrue(r.data["approval_run_id"])
        self.assertEqual(
            self.monday.task("get", task_id=self.task_id).data["status"], "review",
        )
        self.assertTrue(all(s["verdict"] == "pass" for s in r.stages))

    def test_audit_log_records_the_invalid_verdict_and_reason(self):
        r = self._run("reviewer", "malformed")
        stage = [s for s in r.stages if s["role"] == "reviewer"][0]
        self.assertEqual(stage["verdict"], "invalid")
        run = json.loads(
            (self.root / "logs" / "agents" / f"{stage['run_id']}.json").read_text()
        )
        self.assertEqual(run["verdict"]["verdict"], "invalid")
        self.assertTrue(run["verdict"]["reason"])
        self.assertIn("result_full", run["execution"])

    def test_team_run_record_persists_for_invalid_stop(self):
        r = self._run("qa", "truncated")
        path = self.root / "logs" / "agents" / f"{r.data['team_run_id']}.json"
        self.assertTrue(path.exists())
        self.assertEqual(json.loads(path.read_text())["status"], "invalid-verdict")


# ---------------------------------------------------------------------------
# Integration: stop_reason propagates adapter → ProviderResponse.metadata →
# ExecutionReport → verdict parsing
# ---------------------------------------------------------------------------

class _MetadataProvider(FakeAgentProvider):
    """
    A provider that reports truncation the way the real adapters do.

    `brain/providers/anthropic.py` sets metadata from `stop_reason == "max_tokens"`
    and `brain/providers/openai.py` from `finish_reason == "length"`. Both
    normalise onto the same two metadata keys, which is the contract this stub
    stands in for — a live API call cannot run in the suite.
    """

    def __init__(self, *, role: str = "", body: str = "", stop_reason: str = "end_turn") -> None:
        super().__init__(role=role)
        self._body_text = body
        self._stop_reason = stop_reason

    def ask(self, prompt: str, context: str = "", max_tokens: int = 1024, **kwargs: Any) -> ProviderResponse:
        self.calls.append(("ask", prompt))
        return ProviderResponse(
            content=self._body_text,
            model=f"{self.name}-1",
            provider=self.name,
            tokens_used=99,
            metadata={
                "stop_reason": self._stop_reason,
                "truncated": self._stop_reason in ("max_tokens", "length"),
            },
        )


# Long enough to satisfy the orchestrator's ResultValidator.
_LONG_PROSE = (
    "I reviewed the change in depth across every module it touches, considering "
    "correctness, security, and the regression surface. Everything looks good to "
    "me and I see no blocker. Checkpoint 1: PASS. Checkpoint 2: PASS. " * 4
)


class TestTruncationPropagationIntegration(unittest.TestCase):
    """
    End-to-end regression for the truncation signal.

    The unit tests above set `truncated` directly on the parser call, which is
    correct for parser coverage but would not catch a provider adapter that
    stopped populating `ProviderResponse.metadata`. These drive a real
    AgentRuntime run so the whole chain is exercised:

        adapter metadata → ExecutionReport.truncated/.stop_reason
                         → AgentRuntime → parse_verdict → AgentRun.verdict
    """

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.monday = Monday(MondayConfig(project_root=self.root))
        self.task_id = self.monday.task(
            "create", title="Add retry/backoff to the API client",
            objective="Make the client resilient to transient failures.",
            task_type="feature", priority="P1",
        ).task_id

    def tearDown(self):
        self._tmp.cleanup()

    def _runtime_run(self, provider):
        # Driven through AgentRuntime directly: Monday.agent("run") does not
        # forward a provider instance, and the point here is the real chain.
        from agents.runtime import AgentRuntime
        return AgentRuntime(self.monday, self.root, True).run(
            task_id=self.task_id, role="reviewer", provider_instance=provider,
            update_task=False,
        )

    def test_truncation_flag_reaches_the_execution_report(self):
        run = self._runtime_run(
            _MetadataProvider(role="reviewer", body=_LONG_PROSE, stop_reason="max_tokens")
        )
        self.assertTrue(run.execution["truncated"])
        self.assertEqual(run.execution["stop_reason"], "max_tokens")

    def test_truncated_response_without_verdict_is_invalid_not_pass(self):
        """The full chain, on the exact shape that used to silently pass."""
        run = self._runtime_run(
            _MetadataProvider(role="reviewer", body=_LONG_PROSE, stop_reason="max_tokens")
        )
        self.assertEqual(run.verdict["verdict"], INVALID)
        self.assertIn("truncated", run.verdict["reason"])

    def test_openai_style_length_finish_reason_also_propagates(self):
        run = self._runtime_run(
            _MetadataProvider(role="reviewer", body=_LONG_PROSE, stop_reason="length")
        )
        self.assertTrue(run.execution["truncated"])
        self.assertEqual(run.verdict["verdict"], INVALID)

    def test_normal_completion_is_not_marked_truncated(self):
        """Guards the inverse regression: the flag must not be stuck on."""
        body = (
            '```json\n{"verdict": "pass", "confidence": "high", "summary": "sound"}\n```\n'
            + _LONG_PROSE
        )
        run = self._runtime_run(
            _MetadataProvider(role="reviewer", body=body, stop_reason="end_turn")
        )
        self.assertFalse(run.execution["truncated"])
        self.assertEqual(run.execution["stop_reason"], "end_turn")
        self.assertEqual(run.verdict["verdict"], PASS)
        self.assertEqual(run.verdict["source"], "json")

    def test_truncated_but_verdict_emitted_first_survives(self):
        """Why the JSON is requested first: prose is lost, the verdict is not."""
        body = (
            '```json\n{"verdict": "needs_changes", "summary": "add a test"}\n```\n'
            + _LONG_PROSE
        )
        run = self._runtime_run(
            _MetadataProvider(role="reviewer", body=body, stop_reason="max_tokens")
        )
        self.assertTrue(run.execution["truncated"])
        self.assertEqual(run.verdict["verdict"], NEEDS_CHANGES)
        self.assertEqual(run.verdict["source"], "json")

    def test_missing_metadata_defaults_to_not_truncated(self):
        """A provider that reports no metadata must not be assumed truncated."""
        run = self._runtime_run(FakeAgentProvider(role="reviewer"))
        self.assertFalse(run.execution["truncated"])
        self.assertEqual(run.verdict["verdict"], PASS)

    def test_truncated_reviewer_stops_the_team_pipeline(self):
        """The chain end to end, through the team gate."""
        provs = {
            "reviewer": _MetadataProvider(
                role="reviewer", body=_LONG_PROSE, stop_reason="max_tokens"
            )
        }
        r = self.monday.team("run", task_id=self.task_id, provider="fake",
                             stage_providers=provs)
        self.assertFalse(r.success)
        self.assertEqual(r.status, "invalid-verdict")
        self.assertEqual(r.stopped_at, "reviewer")


if __name__ == "__main__":
    unittest.main()
