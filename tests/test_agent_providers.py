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

from agents.adapters import (
    FakeAgentProvider,
    availability_for,
    build_provider_for,
    build_provider_pool,
)
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

    def __init__(self, name: str = "openai", reason: str = "OPENAI_API_KEY is not set"):
        self._name = name
        self._reason = reason

    @property
    def name(self) -> str:
        return self._name

    def availability(self) -> ProviderAvailability:
        return ProviderAvailability(
            available=False, provider=self._name, model=f"{self._name}-model",
            reason=self._reason, env_var="OPENAI_API_KEY" if self._name == "openai" else "",
        )

    def ask(self, prompt, context="", max_tokens=1024, **kw):
        raise AssertionError("ask() must not be called when unavailable")

    def plan(self, objective, context="", max_tokens=2048, **kw):
        return ProviderResponse(content="", provider=self._name)

    def summarize(self, content, max_words=150, **kw):
        return ProviderResponse(content="", provider=self._name)

    def review(self, content, criteria=None, **kw):
        return ProviderResponse(content="", provider=self._name)


class _TieredFake(FakeAgentProvider):
    def __init__(self, name: str, tier: int):
        super().__init__(name=name, role="cpo")
        self._tier = tier

    @property
    def capability_tier(self) -> int:
        return self._tier


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

    @mock.patch("brain.providers.ollama.urlopen")
    def test_ollama_available_local(self, urlopen):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.read.return_value = b'{"models": [{"name": "llama3:latest"}]}'
        urlopen.return_value = response
        av = OllamaProvider(ProviderConfig(type="ollama")).availability()
        self.assertTrue(av.available)
        self.assertEqual(av.env_var, "")

    @mock.patch("brain.providers.ollama.urlopen", side_effect=OSError("offline"))
    def test_ollama_unavailable_when_daemon_is_down(self, _urlopen):
        av = OllamaProvider(ProviderConfig(type="ollama")).availability()
        self.assertFalse(av.available)
        self.assertIn("unavailable", av.reason)

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

    def test_provider_pool_keeps_role_primary_then_stable_fallbacks(self):
        self.assertEqual(
            [p.name for p in build_provider_pool("openai", role="cpo")],
            ["openai", "anthropic", "deepseek", "ollama"],
        )
        self.assertEqual(
            [p.name for p in build_provider_pool("anthropic", role="lead-engineer")],
            ["anthropic", "openai", "deepseek", "ollama"],
        )

    def test_fake_provider_pool_remains_offline_and_single_provider(self):
        pool = build_provider_pool("fake", role="qa")
        self.assertEqual(len(pool), 1)
        self.assertIsInstance(pool[0], FakeAgentProvider)

    def test_environment_model_and_ollama_host_overrides_are_preserved(self):
        with mock.patch.dict(os.environ, {
            "MONDAYOS_DEEPSEEK_MODEL": "deepseek-reasoner",
            "MONDAYOS_OLLAMA_MODEL": "qwen3:8b",
            "OLLAMA_HOST": "mac-mini.local:11434",
        }, clear=False):
            deepseek = build_provider_for("deepseek")
            ollama = build_provider_for("ollama")

        self.assertEqual(deepseek._model, "deepseek-reasoner")
        self.assertEqual(ollama._model, "qwen3:8b")
        self.assertEqual(ollama._base_url, "http://mac-mini.local:11434")

    def test_configured_provider_instance_is_reused_with_its_full_config(self):
        configured = OpenAIProvider(ProviderConfig(
            type="openai",
            model="gpt-custom",
            base_url="https://example.invalid/v1",
        ))
        pool = build_provider_pool(
            "openai",
            role="cpo",
            configured_providers=[configured],
        )
        self.assertIs(pool[0], configured)
        self.assertEqual(pool[0]._model, "gpt-custom")
        self.assertEqual(pool[0]._base_url, "https://example.invalid/v1")

    def test_explicit_provider_cannot_escape_configured_allowlist(self):
        deepseek = FakeAgentProvider(name="deepseek", role="cpo")

        self.assertIsNone(
            build_provider_for(
                "openai",
                role="cpo",
                configured_providers=[deepseek],
            )
        )
        self.assertIsInstance(
            build_provider_for(
                "fake",
                role="cpo",
                configured_providers=[deepseek],
            ),
            FakeAgentProvider,
        )

    def test_invalid_primary_name_does_not_fall_through_configured_pool(self):
        """A typo is a configuration error, not a provider outage."""
        pool = build_provider_pool(
            "opneai",
            role="cpo",
            configured_providers=[FakeAgentProvider(name="deepseek")],
        )
        self.assertEqual(pool, [])

    def test_reviewer_is_pinned_to_mandatory_openai_primary(self):
        pool = build_provider_pool("openai", role="reviewer")
        self.assertEqual([provider.name for provider in pool], ["openai"])
        custom = types.SimpleNamespace(provider="anthropic", role="reviewer")
        self.assertEqual(
            [provider.name for provider in build_provider_pool(custom)],
            ["openai"],
        )

    def test_configured_pool_is_an_allowlist_not_just_a_preference(self):
        deepseek = FakeAgentProvider(name="deepseek", role="cpo")
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "must-not-be-used"}):
            pool = build_provider_pool(
                "openai",
                role="cpo",
                configured_providers=[deepseek],
            )

        self.assertEqual(pool, [deepseek])
        self.assertEqual(
            build_provider_pool(
                "openai",
                role="reviewer",
                configured_providers=[deepseek],
            ),
            [],
        )

    def test_availability_uses_same_effective_allowlisted_provider_as_execution(self):
        deepseek = FakeAgentProvider(name="deepseek", role="cpo")
        agent = types.SimpleNamespace(provider="openai", role="cpo")

        availability = availability_for(
            agent,
            configured_providers=[deepseek],
        )

        self.assertTrue(availability.available)
        self.assertEqual(availability.provider, "deepseek")

    def test_availability_skips_unavailable_primary_for_ready_fallback(self):
        agent = types.SimpleNamespace(provider="openai", role="cpo")
        providers = [
            _UnavailableProvider("openai"),
            FakeAgentProvider(name="deepseek", role="cpo"),
        ]

        availability = availability_for(
            agent,
            configured_providers=providers,
        )

        self.assertTrue(availability.available)
        self.assertEqual(availability.provider, "deepseek")

    def test_custom_reviewer_availability_still_requires_openai(self):
        reviewer = types.SimpleNamespace(provider="anthropic", role="reviewer")
        availability = availability_for(
            reviewer,
            configured_providers=[FakeAgentProvider(name="anthropic")],
        )

        self.assertFalse(availability.available)
        self.assertEqual(availability.provider, "openai")


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
        # An explicit provider is pinned and must not silently fall back.
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_API_KEY", None)
            r = self.monday.agent(
                "run", task_id=self.task_id, role="cpo", provider="openai",
            )
        self.assertFalse(r.success)
        self.assertEqual(r.status, "unavailable")
        self.assertIn("OPENAI_API_KEY", r.message)
        got = self.monday.task("get", task_id=self.task_id)
        self.assertEqual(got.data["status"], "backlog")  # untouched

    def test_role_default_falls_back_in_order_when_primary_is_unavailable(self):
        pool = [
            _UnavailableProvider(),
            FakeAgentProvider(name="deepseek", role="cpo"),
        ]
        with mock.patch("agents.runtime.build_provider_pool", return_value=pool):
            r = self.monday.agent("run", task_id=self.task_id, role="cpo")

        self.assertTrue(r.success)
        self.assertEqual(r.provider_used, "deepseek")
        self.assertEqual(
            [row["status"] for row in r.data["execution"]["provider_attempts"]],
            ["unavailable", "completed"],
        )

    def test_role_default_reports_all_unavailable_candidates(self):
        pool = [
            _UnavailableProvider(),
            _UnavailableProvider("deepseek", "DEEPSEEK_API_KEY is not set"),
        ]
        with mock.patch("agents.runtime.build_provider_pool", return_value=pool):
            r = self.monday.agent("run", task_id=self.task_id, role="cpo")

        self.assertFalse(r.success)
        self.assertEqual(r.status, "unavailable")
        self.assertIn("openai", r.message)
        self.assertIn("deepseek", r.message)
        got = self.monday.task("get", task_id=self.task_id)
        self.assertEqual(got.data["status"], "backlog")

    def test_role_default_honors_explicit_highest_capability_policy(self):
        low = _TieredFake("low", 1)
        high = _TieredFake("high", 9)
        with mock.patch(
            "agents.runtime.build_provider_pool",
            return_value=[low, high],
        ):
            r = self.monday.agent(
                "run",
                task_id=self.task_id,
                role="cpo",
                policy="highest-capability",
            )

        self.assertTrue(r.success)
        self.assertEqual(r.provider_used, "high")
        self.assertEqual(low.calls, [])
        self.assertTrue(high.calls)

    def test_role_default_rejects_invalid_explicit_policy(self):
        with mock.patch(
            "agents.runtime.build_provider_pool",
            return_value=[FakeAgentProvider(name="openai", role="cpo")],
        ):
            r = self.monday.agent(
                "run",
                task_id=self.task_id,
                role="cpo",
                policy="not-a-policy",
            )

        self.assertFalse(r.success)
        self.assertIn("Unknown selection policy", r.message)

    def test_failed_run_cannot_be_approved(self):
        r = self.monday.agent(
            "run",
            task_id=self.task_id,
            role="cpo",
            provider="not-a-provider",
        )
        self.assertFalse(r.success)

        decision = self.monday.agent(
            "review",
            run_id=r.run_id,
            approve=True,
            by="human:test",
        )

        self.assertFalse(decision.success)
        self.assertIn("not a successful pending review", decision.message)
        self.assertEqual(
            self.monday.task("get", task_id=self.task_id).data["status"],
            "backlog",
        )

    def test_mandatory_reviewer_rejects_non_openai_explicit_override(self):
        r = self.monday.agent(
            "run",
            task_id=self.task_id,
            role="reviewer",
            provider="anthropic",
        )

        self.assertFalse(r.success)
        self.assertEqual(r.status, "blocked")
        self.assertIn("requires OpenAI/ChatGPT", r.message)
        self.assertEqual(
            self.monday.task("get", task_id=self.task_id).data["status"],
            "backlog",
        )

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
            self.assertIn("effective_provider", a)
            self.assertIn("model", a)
            self.assertIn("requires", a)

    def test_list_reports_the_ready_fallback_as_effective(self):
        effective = ProviderAvailability(
            available=True,
            provider="deepseek",
            model="deepseek-chat",
            reason="ready",
        )
        with mock.patch("agents.adapters.availability_for", return_value=effective):
            rows = self.monday.agent("list", role="cpo").data["agents"]

        self.assertEqual(rows[0]["provider"], "openai")
        self.assertEqual(rows[0]["effective_provider"], "deepseek")
        self.assertTrue(rows[0]["available"])

    def test_list_shows_real_provider_mappings(self):
        rows = {a["role"]: a["provider"] for a in self.monday.agent("list").data["agents"]}
        self.assertEqual(rows["cpo"], "openai")
        self.assertEqual(rows["research"], "openai")
        self.assertEqual(rows["lead-engineer"], "anthropic")
        self.assertEqual(rows["reviewer"], "openai")
        self.assertEqual(rows, DEFAULT_ROLE_PROVIDERS)


# ---------------------------------------------------------------------------
# Team workflow with real-provider wiring (mocked, no live calls)
# ---------------------------------------------------------------------------

class TestTeamProviders(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.monday = Monday(MondayConfig(project_root=self.root))
        self.task_id = self.monday.task(
            "create", title="X", objective="Do X.", priority="P1",
        ).task_id

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
            "reviewer": FakeAgentProvider(name="openai", role="reviewer"),
        }
        r = self.monday.team("run", task_id=self.task_id, stage_providers=sp)
        self.assertTrue(r.success)
        self.assertEqual(r.status, "awaiting-approval")
        provs = {s["role"]: s["provider_used"] for s in r.data["stages"]}
        self.assertEqual(provs["cpo"], "openai")
        self.assertEqual(provs["lead-engineer"], "anthropic")
        self.assertEqual(provs["reviewer"], "openai")
        self.assertTrue(all(s["provider_model"] for s in r.data["stages"]))

    def test_seeded_agents_available_when_env_and_sdk_present(self):
        # Acceptance: real providers resolve as available when API keys exist.
        with mock.patch.dict(sys.modules, _fake_sdk("openai", "anthropic")), \
             mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-o", "ANTHROPIC_API_KEY": "sk-a"}):
            by_role = {a["role"]: a for a in self.monday.agent("list").data["agents"]}
            self.assertTrue(by_role["cpo"]["available"])
            self.assertTrue(by_role["lead-engineer"]["available"])
            self.assertTrue(by_role["research"]["available"])
            self.assertTrue(by_role["reviewer"]["available"])


if __name__ == "__main__":
    unittest.main()
