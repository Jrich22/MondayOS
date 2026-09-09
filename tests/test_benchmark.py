"""
Tests for the benchmark harness.

A harness that judges MondayOS has to be trustworthy in ways the thing it
measures does not: it must not reach a model, must not vary between runs, and
must not quietly stop covering a corpus. Those three are asserted directly here,
because each of them fails silently — a benchmark that skipped a project or
sampled a provider would still print a plausible number.

The other theme is that weaknesses must stay visible. A benchmark whose cases all
pass is evidence the questions were chosen to pass, so `known_failing` cases are
required to exist and are reported individually rather than folded into a rate.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from benchmark.cases import ALL_CASES, Dimension
from benchmark.corpus import CORPUS_QUESTIONS, discover
from benchmark.guard import FORBIDDEN, assert_provider_free, imported_packages
from benchmark.probes import CaseResult, CitationBreakdown, CorpusProbe
from benchmark.report import SCHEMA, CorpusReport, Report, Verdict, load_baseline
from benchmark.runner import run, verdict
from benchmark.scoring import Assertions, compare, score

REPO = Path(__file__).resolve().parent.parent


class TestProviderFree(unittest.TestCase):
    """
    The harness must measure deterministic machinery, not a model.

    One accidental import of Monday or WorkspaceService would pull a provider
    into the call path and turn a repeatable number into a sampled one — while
    still looking like it worked.
    """

    def test_the_benchmark_imports_no_provider_reachable_package(self):
        assert_provider_free()

    def test_the_guard_would_actually_catch_a_violation(self):
        """A guard nobody has seen fail is not known to work."""
        found = imported_packages()
        self.assertTrue(found, "the guard should see the benchmark's own imports")
        self.assertFalse(found & FORBIDDEN)
        for package in ("brain", "monday", "workspace", "anthropic", "openai"):
            self.assertIn(package, FORBIDDEN)

    def test_the_guard_is_transitive_not_just_direct(self):
        """
        Direct imports prove little: the benchmark imports `reasoning`, and if
        `reasoning` ever imported `workspace` a provider would be two hops away.
        The reachable set must include packages the benchmark never names.
        """
        reachable = imported_packages()
        self.assertIn("reasoning", reachable, "should see a direct import")
        self.assertIn("intelligence", reachable, "should see a transitive import")
        self.assertIn("core", reachable)


class TestCaseInventory(unittest.TestCase):
    def test_case_ids_are_unique(self):
        ids = [c.id for c in ALL_CASES]
        self.assertEqual(len(ids), len(set(ids)))

    def test_known_failures_are_declared(self):
        """
        A benchmark with no failing cases is evidence the questions were chosen
        to pass. Today's real weaknesses must be represented.

        S4 fixed the five routing phrasings this used to name, so naming them
        here would now assert that a fixed bug is still broken. What remains
        genuinely unsolved is `retrieval.why-decision`, and for a reason no
        amount of retrieval work can change: Cue App has no decision records at
        all, and WeatherBot's three ADRs are about script layout, smoke tests and
        runtime artefacts rather than its forecast pipeline. The honest answer on
        those corpora is that nothing was written down.
        """
        failing = [c.id for c in ALL_CASES if c.known_failing]
        self.assertTrue(failing, "no known failures declared — were the cases chosen to pass?")
        self.assertIn("retrieval.why-decision", failing)
        for name in ():
            self.assertIn(f"routing.strategic.{name}", failing)

    def test_every_known_failure_says_why(self):
        for case in ALL_CASES:
            if case.known_failing:
                self.assertTrue(case.because, f"{case.id} is known-failing without a reason")

    def test_nouns_are_substituted_per_corpus(self):
        case = next(c for c in ALL_CASES if "{symbol}" in c.question)
        rendered = case.render({"symbol": "EventCard", "topic": "x"})
        self.assertIn("EventCard", rendered)
        self.assertNotIn("{symbol}", rendered)

    def test_an_unknown_noun_leaves_the_question_intact(self):
        case = next(c for c in ALL_CASES if "{symbol}" in c.question)
        self.assertIn("{symbol}", case.render({}))

    def test_both_dimensions_are_covered(self):
        dimensions = {c.dimension for c in ALL_CASES}
        self.assertIn(Dimension.ROUTING, dimensions)
        self.assertIn(Dimension.RETRIEVAL, dimensions)


class TestScoringIsGeneric(unittest.TestCase):
    def test_scoring_contains_no_project_specific_branching(self):
        """
        The standard must be identical for every corpus, or comparing them means
        nothing. Corpus-specific nouns live in corpus.py; scoring never sees them.
        """
        source = (REPO / "benchmark" / "scoring.py").read_text(encoding="utf-8")
        for slug in CORPUS_QUESTIONS:
            self.assertNotIn(f'"{slug}"', source, f"scoring branches on {slug}")
            self.assertNotIn(f"'{slug}'", source)

    def test_a_lookup_routed_strategic_is_a_false_positive(self):
        probe = CorpusProbe(project="p", available=True)
        probe.results = [
            CaseResult("routing.lookup.a", "routing", "p", False, "grounded", "executive"),
            CaseResult("routing.strategic.b", "routing", "p", False, "executive", "grounded"),
        ]
        assertions, observations = score(probe)
        self.assertEqual(observations.routing_false_positives, ["routing.lookup.a"])
        self.assertEqual(observations.routing_false_negatives, ["routing.strategic.b"])
        self.assertEqual(assertions.grounded_false_positives, 1)

    def test_known_failures_are_recorded_individually_not_as_a_rate(self):
        """
        S3 and S4 have to show exactly which failures disappeared, which an
        aggregate percentage cannot.
        """
        probe = CorpusProbe(project="p", available=True)
        probe.results = [
            CaseResult(
                "x.y",
                "routing",
                "p",
                False,
                "executive",
                "grounded",
                known_failing=True,
                because="no pattern",
            ),
        ]
        _, observations = score(probe)
        entry = observations.known_failing[0]
        for key in ("case_id", "dimension", "project", "expected", "observed", "because"):
            self.assertIn(key, entry)
        self.assertTrue(entry["still_failing"])

    def test_citations_are_bucketed_by_how_useful_they_are(self):
        breakdown = CitationBreakdown(
            total=10, navigable=3, file_only=5, unresolved=1, outside_root=1
        )
        data = breakdown.to_dict()
        self.assertEqual(data["navigability"], 0.3)
        self.assertEqual(data["outside_root"], 1)


class TestAssertions(unittest.TestCase):
    def test_a_clean_run_has_no_violations(self):
        self.assertEqual(Assertions().violations(), [])

    def test_each_invariant_reports_its_own_violation(self):
        self.assertEqual(len(Assertions(leaked_commits=2).violations()), 1)
        self.assertEqual(len(Assertions(grounded_false_positives=1).violations()), 1)
        self.assertEqual(len(Assertions(citations_outside_root=3).violations()), 1)
        self.assertEqual(
            len(
                Assertions(
                    leaked_commits=1, grounded_false_positives=1, citations_outside_root=1
                ).violations()
            ),
            3,
        )


class TestComparison(unittest.TestCase):
    BASE = {
        "routing_accuracy": 0.72,
        "citation_navigability": 0.12,
        "retrieval_grounded_rate": 1.0,
        "initiatives": [{"name": "src", "slug": "src"}],
        "known_failing": [{"case_id": "a.b", "still_failing": True}],
        "routing_false_positives": [],
    }

    def test_matching_results_produce_nothing(self):
        failures, improvements = compare("p", dict(self.BASE), self.BASE)
        self.assertEqual(failures, [])
        self.assertEqual(improvements, [])

    def test_a_regression_fails(self):
        worse = dict(self.BASE, routing_accuracy=0.60)
        failures, _ = compare("p", worse, self.BASE)
        self.assertTrue(any("routing_accuracy" in f for f in failures))

    def test_an_improvement_is_reported_so_the_baseline_gets_re_recorded(self):
        better = dict(self.BASE, routing_accuracy=0.95)
        failures, improvements = compare("p", better, self.BASE)
        self.assertEqual(failures, [])
        self.assertTrue(any("routing_accuracy" in i for i in improvements))

    def test_a_fixed_known_failure_is_named(self):
        fixed = dict(self.BASE, known_failing=[{"case_id": "a.b", "still_failing": False}])
        _, improvements = compare("p", fixed, self.BASE)
        self.assertTrue(any("a.b now passes" in i for i in improvements))

    def test_a_new_failure_that_was_not_known_fails(self):
        broken = dict(
            self.BASE,
            known_failing=[
                {"case_id": "a.b", "still_failing": True},
                {"case_id": "c.d", "still_failing": True},
            ],
        )
        failures, _ = compare("p", broken, self.BASE)
        self.assertTrue(any("c.d now fails" in f for f in failures))

    def test_a_changed_initiative_set_is_surfaced(self):
        """S3 must not be able to change discovery without the baseline noticing."""
        changed = dict(self.BASE, initiatives=[{"name": "components", "slug": "components"}])
        _, improvements = compare("p", changed, self.BASE)
        self.assertTrue(any("initiatives changed" in i for i in improvements))

    def test_a_grounded_false_positive_always_fails(self):
        bad = dict(self.BASE, routing_false_positives=["routing.lookup.where"])
        failures, _ = compare("p", bad, self.BASE)
        self.assertTrue(any("routed strategic" in f for f in failures))


class TestVerdict(unittest.TestCase):
    def test_the_stale_baseline_message_is_explicit(self):
        """
        A generic failure trains people to ignore it. This one has to say what
        happened and what to do.
        """
        result = Verdict(improvements=["p: routing_accuracy rose"])
        rendered = result.render()
        self.assertIn("Benchmark improved", rendered)
        self.assertIn("stale", rendered)
        self.assertIn("re-record", rendered)
        self.assertFalse(result.ok)

    def test_an_assertion_violation_fails_the_run(self):
        report = Report()
        report.corpora["p"] = CorpusReport(
            project="p",
            available=True,
            assertions={
                "leaked_commits": 1,
                "grounded_false_positives": 0,
                "citations_outside_root": 0,
            },
            observations={},
        )
        result = verdict(report, baseline={"schema": SCHEMA, "corpora": {}})
        self.assertFalse(result.ok)
        self.assertTrue(any("another project" in f for f in result.failures))

    def test_a_missing_corpus_is_skipped_with_a_reason_and_does_not_fail(self):
        report = Report()
        report.corpora["ghost"] = CorpusReport(
            project="ghost", available=False, reason="ghost is not registered"
        )
        result = verdict(report, baseline={"schema": SCHEMA, "corpora": {}})
        self.assertEqual(result.failures, [])
        self.assertTrue(any("not registered" in s for s in result.skipped))


class TestCorpusDiscovery(unittest.TestCase):
    def test_corpora_resolve_through_the_project_registry(self):
        found = discover(REPO / "config")
        self.assertTrue(found)
        for corpus in found:
            if corpus.available:
                self.assertTrue(corpus.root and corpus.root.is_dir())
                self.assertIsNotNone(corpus.scope)

    def test_an_unregistered_project_states_why_it_was_skipped(self):
        with TemporaryDirectory() as tmp:
            found = discover(Path(tmp), slugs=("ghost",))
            self.assertFalse(found[0].available)
            self.assertIn("not registered", found[0].reason)

    def test_every_declared_corpus_supplies_its_own_nouns(self):
        for slug, nouns in CORPUS_QUESTIONS.items():
            with self.subTest(slug=slug):
                self.assertIn("symbol", nouns)
                self.assertIn("topic", nouns)


class TestReportShape(unittest.TestCase):
    def test_volatile_values_are_excluded_from_the_comparable_block(self):
        """
        Ordinary source growth must not fail a run, or the benchmark becomes
        something people learn to ignore.
        """
        report = Report()
        report.corpora["p"] = CorpusReport(
            project="p", available=True, volatile={"files": 150, "index_ms": 120}
        )
        self.assertIn("volatile", report.to_dict()["corpora"]["p"])
        self.assertNotIn("volatile", report.stable()["corpora"]["p"])

    def test_the_report_serialises_deterministically(self):
        report = Report()
        report.corpora["b"] = CorpusReport(project="b", available=True)
        report.corpora["a"] = CorpusReport(project="a", available=True)
        self.assertEqual(report.json(), report.json())
        self.assertLess(report.json().index('"a"'), report.json().index('"b"'))

    def test_the_schema_version_is_recorded(self):
        self.assertEqual(Report().to_dict()["schema"], SCHEMA)


class TestAgainstRealCorpora(unittest.TestCase):
    """One real run, asserting the invariants rather than any particular number."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.report = run(config_dir=REPO / "config")

    def test_at_least_one_corpus_was_measured(self):
        measured = [c for c in self.report.corpora.values() if c.available]
        if not measured:
            self.skipTest("no benchmark corpora are checked out on this machine")
        self.assertTrue(measured)

    def test_no_corpus_cites_another_projects_commits(self):
        for slug, corpus in self.report.corpora.items():
            if not corpus.available:
                continue
            with self.subTest(project=slug):
                self.assertEqual(corpus.assertions["leaked_commits"], 0)

    def test_no_corpus_routes_a_lookup_to_a_strategic_register(self):
        """The hard invariant: false positives stay at zero."""
        for slug, corpus in self.report.corpora.items():
            if not corpus.available:
                continue
            with self.subTest(project=slug):
                self.assertEqual(corpus.assertions["grounded_false_positives"], 0)
                self.assertEqual(corpus.observations["routing_false_positives"], [])

    def test_no_citation_points_outside_its_project(self):
        for slug, corpus in self.report.corpora.items():
            if not corpus.available:
                continue
            with self.subTest(project=slug):
                self.assertEqual(corpus.assertions["citations_outside_root"], 0)

    def test_the_benchmark_does_not_index_mondayos_conversations(self):
        """
        Re-asserts PR #44's exclusion from a second angle: the harness must not
        reintroduce self-generated evidence by indexing differently.
        """
        corpus = self.report.corpora.get("mondayos")
        if not corpus or not corpus.available:
            self.skipTest("mondayos corpus unavailable")
        self.assertEqual(corpus.assertions["citations_outside_root"], 0)
        self.assertGreater(corpus.volatile["files"], 0)


class TestBaselineGate(unittest.TestCase):
    """
    The committed baseline, and the behaviour that makes it worth committing.

    A baseline that only prints worse numbers is a baseline people stop reading.
    These tests prove the gate actually fails — including on *improvement*, which
    is deliberate: a baseline that silently absorbs good news stops describing the
    system and stops protecting it.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.report = run(config_dir=REPO / "config")
        cls.baseline = load_baseline()
        cls.available = [s for s, c in cls.report.corpora.items() if c.available]

    def _require_corpora(self) -> None:
        """
        Skip when nothing is checked out.

        The gate tests work by mutating the baseline and asserting the run then
        fails. With no corpora available there is nothing to compare, so the
        verdict is legitimately empty and the mutation proves nothing — the test
        would fail for the environment rather than for the behaviour.
        """
        if not self.available:
            self.skipTest("no benchmark corpora are checked out on this machine")

    def test_a_baseline_is_committed(self):
        self.assertTrue(self.baseline, "benchmark/baseline.json is missing or unreadable")
        self.assertEqual(self.baseline["schema"], SCHEMA)

    def test_the_current_run_matches_the_committed_baseline(self):
        available = [s for s, c in self.report.corpora.items() if c.available]
        if not available:
            self.skipTest("no corpora checked out")
        result = verdict(self.report, self.baseline)
        self.assertTrue(result.ok, result.render())

    def test_a_regression_fails(self):
        """
        A real run measured against a baseline claiming better must fail.

        Measured on navigability rather than routing accuracy: routing is at
        1.0 after S4, and a baseline cannot claim better than perfect. Overall
        navigability cannot reach 1.0 while any answer cites a commit, so this
        stays a real comparison rather than one that quietly stops testing.
        """
        self._require_corpora()
        import copy

        worse = copy.deepcopy(self.baseline)
        for corpus in worse.get("corpora", {}).values():
            if corpus.get("available"):
                corpus["observations"]["citation_navigability"] = 1.0
        result = verdict(self.report, worse)
        self.assertFalse(result.ok)
        self.assertTrue(any("citation_navigability fell" in f for f in result.failures))

    def test_an_improvement_fails_with_an_explicit_stale_baseline_message(self):
        """
        A generic failure trains developers to ignore it. This one must name what
        happened and how to fix it.
        """
        self._require_corpora()
        import copy

        better = copy.deepcopy(self.baseline)
        for corpus in better.get("corpora", {}).values():
            if corpus.get("available"):
                corpus["observations"]["routing_accuracy"] = 0.1
        result = verdict(self.report, better)
        self.assertFalse(result.ok)
        rendered = result.render()
        self.assertIn("Benchmark improved", rendered)
        self.assertIn("baseline is stale", rendered)
        self.assertIn("--record", rendered)

    def test_a_changed_initiative_set_is_caught(self):
        """S3 must not be able to change discovery without this noticing."""
        self._require_corpora()
        import copy

        moved = copy.deepcopy(self.baseline)
        target = moved.get("corpora", {}).get("cue-app")
        if not target or not target.get("available"):
            self.skipTest("cue-app not available")
        target["observations"]["initiatives"] = [{"name": "components", "slug": "components"}]
        result = verdict(self.report, moved)
        self.assertFalse(result.ok)
        self.assertTrue(any("initiatives changed" in i for i in result.improvements))

    def test_the_baseline_records_todays_weaknesses_rather_than_hiding_them(self):
        """
        The baseline is only useful if it describes what MondayOS actually does,
        including what it does badly. If these ever pass, S4 landed and the
        baseline must be re-recorded deliberately.

        Both halves have now served their purpose, and both are replaced by the
        property that made them worth writing rather than deleted.

        The discovery half asserted cue-app reported one initiative called "src".
        S3 fixed that; what remains asserted is that a container is not a
        capability, so whatever cue-app reports, it is not that.

        The routing half named five strategic phrasings recorded as failing. S4
        fixed all five, so naming them would now assert that a fixed bug is still
        broken. What remains is `retrieval.why-decision`, which is still failing
        and will stay failing for a reason no retrieval work can change: Cue App
        has no decision records at all, and WeatherBot's three ADRs are about
        script layout, smoke tests and runtime artefacts rather than its forecast
        pipeline. A benchmark that recorded those as passing would be lying.
        """
        self._require_corpora()
        corpora = self.baseline.get("corpora", {})
        cue = corpora.get("cue-app")
        if cue and cue.get("available"):
            names = [i["name"] for i in cue["observations"]["initiatives"]]
            self.assertNotEqual(names, ["src"], "the S3 discovery fix is not recorded")
            self.assertNotIn("src", names)
            self.assertGreater(len(names), 1)

        for slug, corpus in corpora.items():
            if not corpus.get("available"):
                continue
            with self.subTest(project=slug):
                failing = {
                    k["case_id"]
                    for k in corpus["observations"]["known_failing"]
                    if k.get("still_failing")
                }
                # Routing is solved: no routing case may still be failing.
                self.assertFalse(
                    {c for c in failing if c.startswith("routing.")},
                    f"{slug} records a routing failure S4 was meant to fix",
                )
                # Retrieval is not, on the two corpora with no matching ADR.
                if slug in ("cue-app", "weatherbot"):
                    self.assertIn("retrieval.why-decision", failing)

    def test_the_baseline_records_zero_leaks_and_zero_false_positives(self):
        self._require_corpora()
        for slug, corpus in self.baseline.get("corpora", {}).items():
            if not corpus.get("available"):
                continue
            with self.subTest(project=slug):
                self.assertEqual(corpus["assertions"]["leaked_commits"], 0)
                self.assertEqual(corpus["assertions"]["grounded_false_positives"], 0)
                self.assertEqual(corpus["assertions"]["citations_outside_root"], 0)


class TestDeterminism(unittest.TestCase):
    def test_two_runs_produce_an_identical_comparable_block(self):
        """
        Determinism must be a property of the measurement, not of what happened
        to be cached.
        """
        first = run(config_dir=REPO / "config").stable()
        second = run(config_dir=REPO / "config").stable()
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))

    def test_a_warm_cache_gives_the_same_answer_as_a_cold_one(self):
        with TemporaryDirectory() as tmp:
            shared = Path(tmp)
            cold = run(config_dir=REPO / "config", cache_root=shared).stable()
            warm = run(config_dir=REPO / "config", cache_root=shared).stable()
            self.assertEqual(json.dumps(cold, sort_keys=True), json.dumps(warm, sort_keys=True))

    def test_volatile_values_are_never_compared(self):
        """Ordinary source growth must not fail a run."""
        import copy

        report = run(config_dir=REPO / "config")
        shifted = copy.deepcopy(load_baseline())
        for corpus in shifted.get("corpora", {}).values():
            if corpus.get("available"):
                corpus["volatile"] = {"files": 999999, "index_ms": 999999}
        self.assertTrue(verdict(report, shifted).ok)


if __name__ == "__main__":
    unittest.main()
