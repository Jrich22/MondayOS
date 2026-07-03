"""
Tests for real-provider wiring in the agent layer (v2.2): availability checks,
graceful key-missing handling, provider/model logging, and resolution.

NONE of these tests make a live OpenAI/Anthropic call. Availability is exercised
by injecting/removing the SDK module in sys.modules and toggling env vars; the
execution path uses fakes/stubs only.
"""
from __future__ import annotations

import os
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agents.adapters import FakeAgentProvider, availability_for, build_provider_for
from agents.roles import DEFAULT_ROLE_PROVIDERS
from brain.providers.anthropic import AnthropicProvider
from brain.providers.base import AIProvider, ProviderAvailability, ProviderResponse
from brain.providers.factory import ProviderConfig
from brain.providers.ollama import OllamaProvider
from brain.providers.openai import OpenAIProvider
from monday import Monday, MondayConfig


def _fake_sdk(*names: str) -> dict[str, object]:
    """A sys.modules patch that makes `import name` succeed (dummy modules)."""
    return {n: types.ModuleType(n) for n in names}


class _UnavailableProvider(AIProvider):
    """AIProvider stub that reports itself unavailable — ask() must never run."""

    @property
    def name(self) -> str:
        return "openai"

    def availability(self) -> ProviderAvailability:
        return ProviderAvailability(
            available=False, provider="openai", model="gpt-4o-mini",
            reason="OPENAI_API_KEY is not set", env_var="OPENAI_API_KEY",
        )

    def ask(self, prompt, context="", max_tokens=1024, **kw):
        raise AssertionError("ask() must not be called when unavailable")

    def plan(self, objective, context="", max_tokens=2048, **kw):
        return ProviderResponse(content="", provider="openai")

    def summarize(self, content, max_words=150, **kw):
        return ProviderResponse(content="", provider="openai")

    def review(self, content, criteria=None, **kw):
        return ProviderResponse(content="", provider="openai")


# ---------------------------------------------------------------------------
# Availability unit tests (no live calls)
# ---------------------------------------------------------------------------

class TestProviderAvailability(unittest.TestCase):
    def test_openai_available_with_sdk_and_key(self):
        with mock.patch.dict(sys.modules, _fake_sdk("openai")), \
             mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            av = OpenAIProvider(ProviderConfig(type="openai")).availability()
        self.assertTrue(av.available)
        self.assertEqual(av.model, "gpt-4o-mini")

    def test_openai_unavailable_missing_key(self):
        with mock.patch.dict(sys.modules, _fake_sdk("openai")), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_API_KEY", None)
            av = OpenAIProvider(ProviderConfig(type="openai")).availability()
        self.assertFalse(av.available)
        self.assertEqual(av.env_var, "OPENAI_API_KEY")
        self.assertIn("OPENAI_API_KEY", av.reason)

    def test_openai_unavailable_missing_sdk(self):
        with mock.patch.dict(sys.modules, {"openai": None}):
            av = OpenAIProvider(ProviderConfig(type="openai", api_key="sk-x")).availability()
        self.assertFalse(av.available)
        self.assertIn("SDK", av.reason)
        self.assertEqual(av.install_hint, "pip install openai")

    def test_anthropic_available_with_sdk_and_key(self):
        with mock.patch.dict(sys.modules, _fake_sdk("anthropic")), \
             mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant"}):
            av = AnthropicProvider(ProviderConfig(type="anthropic")).availability()
        self.assertTrue(av.available)
        self.assertEqual(av.env_var, "ANTHROPIC_API_KEY")

    def test_anthropic_unavailable_missing_sdk(self):
        with mock.patch.dict(sys.modules, {"anthropic": None}):
            av = AnthropicProvider(ProviderConfig(type="anthropic", api_key="k")).availability()
        self.assertFalse(av.available)
        self.assertEqual(av.install_hint, "pip install anthropic")

    def test_ollama_available_local(self):
        av = OllamaProvider(ProviderConfig(type="ollama")).availability()
        self.assertTrue(av.available)
        self.assertEqual(av.env_var, "")

    def test_fake_available(self):
        self.assertTrue(FakeAgentProvider().availability().available)

    def test_instructions_text(self):
        av = ProviderAvailability(
            available=False, provider="openai", reason="OPENAI_API_KEY is not set",
            env_var="OPENAI_API_KEY",
        )
        self.assertIn("set OPENAI_API_KEY", av.instructions())


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

class TestResolution(unittest.TestCase):
    def test_build_provider_classes(self):
        self.assertIsInstance(build_provider_for("openai"), OpenAIProvider)
        self.assertIsInstance(build_provider_for("anthropic"), AnthropicProvider)
        self.assertIsInstance(build_provider_for("ollama"), OllamaProvider)
        self.assertIsInstance(build_provider_for("fake"), FakeAgentProvider)

    def test_build_unknown_none(self):
        self.assertIsNone(build_provider_for("nope"))

    def test_availability_for_unknown(self):
        self.assertFalse(availability_for("nope").available)


# ---------------------------------------------------------------------------
# Runtime availability gate (Monday.agent run)
# ---------------------------------------------------------------------------

class TestRuntimeGate(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.monday = Monday(MondayConfig(project_root=self.root))
        self.task_id = self.monday.task(
            "create", title="X", objective="Do X.", priority="P1"
        ).task_id

    def tearDown(self):
        self._tmp.cleanup()

    def test_unavailable_provider_fails_gracefully(self):
        # cpo defaults to openai; with no OPENAI_API_KEY it must not execute.
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_API_KEY", None)
            r = self.monday.agent("run", task_id=self.task_id, role="cpo")
        self.assertFalse(r.success)
        self.assertEqual(r.status, "unavailable")
        self.assertIn("OPENAI_API_KEY", r.message)
        got = self.monday.task("get", task_id=self.task_id)
        self.assertEqual(got.data["status"], "backlog")  # untouched

    def test_available_fake_provider_runs_and_logs_model(self):
        r = self.monday.agent("run", task_id=self.task_id, role="lead-engineer", provider="fake")
        self.assertTrue(r.success)
        self.assertEqual(r.status, "review")
        self.assertEqual(r.data["provider_model"], "fake-1")

    def test_dry_run_not_blocked_by_missing_key(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_API_KEY", None)
            r = self.monday.agent("run", task_id=self.task_id, role="cpo", mode="dry-run")
        self.assertTrue(r.success)
        self.assertEqual(r.status, "dry-run")

    def test_execution_report_records_model(self):
        r = self.monday.agent("run", task_id=self.task_id, role="qa", provider="fake")
        self.assertEqual(r.data["execution"]["model_used"], "fake-1")


# ---------------------------------------------------------------------------
# agent list availability + mappings
# ---------------------------------------------------------------------------

class TestAgentListAvailability(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.monday = Monday(MondayConfig(project_root=Path(self._tmp.name)))

    def tearDown(self):
        self._tmp.cleanup()

    def test_list_includes_availability_fields(self):
        for a in self.monday.agent("list").data["agents"]:
            self.assertIn("available", a)
            self.assertIn("model", a)
            self.assertIn("requires", a)

    def test_list_shows_real_provider_mappings(self):
        rows = {a["role"]: a["provider"] for a in self.monday.agent("list").data["agents"]}
        self.assertEqual(rows["cpo"], "openai")
        self.assertEqual(rows["research"], "openai")
        self.assertEqual(rows["lead-engineer"], "anthropic")
        self.assertEqual(rows, DEFAULT_ROLE_PROVIDERS)


# ---------------------------------------------------------------------------
# Team workflow with real-provider wiring (mocked, no live calls)
# ---------------------------------------------------------------------------

class TestTeamProviders(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.monday = Monday(MondayConfig(project_root=self.root))
        self.task_id = self.monday.task("create", title="X", objective="Do X.", priority="P1").task_id

    def tearDown(self):
        self._tmp.cleanup()

    def test_team_stops_gracefully_when_provider_unavailable(self):
        r = self.monday.team(
            "run", task_id=self.task_id,
            stage_providers={"cpo": _UnavailableProvider()},
        )
        self.assertFalse(r.success)
        self.assertEqual(r.status, "failed")
        self.assertEqual(r.stopped_at, "cpo")
        self.assertIn("OPENAI_API_KEY", r.message)
        got = self.monday.task("get", task_id=self.task_id)
        self.assertNotEqual(got.data["status"], "review")

    def test_team_resolves_named_providers_when_available(self):
        # Simulate keys existing by injecting available providers named like the
        # real ones (still offline). Pipeline completes and records the names.
        sp = {
            "cpo": FakeAgentProvider(name="openai", role="cpo"),
            "lead-engineer": FakeAgentProvider(name="anthropic", role="lead-engineer"),
            "qa": FakeAgentProvider(name="anthropic", role="qa"),
            "security": FakeAgentProvider(name="anthropic", role="security"),
            "reviewer": FakeAgentProvider(name="anthropic", role="reviewer"),
        }
        r = self.monday.team("run", task_id=self.task_id, stage_providers=sp)
        self.assertTrue(r.success)
        self.assertEqual(r.status, "awaiting-approval")
        provs = {s["role"]: s["provider_used"] for s in r.data["stages"]}
        self.assertEqual(provs["cpo"], "openai")
        self.assertEqual(provs["lead-engineer"], "anthropic")
        self.assertTrue(all(s["provider_model"] for s in r.data["stages"]))

    def test_seeded_agents_available_when_env_and_sdk_present(self):
        # Acceptance: real providers resolve as available when API keys exist.
        with mock.patch.dict(sys.modules, _fake_sdk("openai", "anthropic")), \
             mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-o", "ANTHROPIC_API_KEY": "sk-a"}):
            by_role = {a["role"]: a for a in self.monday.agent("list").data["agents"]}
            self.assertTrue(by_role["cpo"]["available"])
            self.assertTrue(by_role["lead-engineer"]["available"])
            self.assertTrue(by_role["research"]["available"])


if __name__ == "__main__":
    unittest.main()
