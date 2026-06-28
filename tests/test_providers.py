"""Tests for the AI provider abstraction layer (brain.providers)."""
from __future__ import annotations

import sys
import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, PropertyMock

from brain.providers import (
    AIProvider,
    ProviderAuthError,
    ProviderConfig,
    ProviderError,
    ProviderRateLimitError,
    ProviderResponse,
    ProviderUnavailableError,
    create_provider,
)
from brain.providers.anthropic import AnthropicProvider
from brain.providers.ollama import OllamaProvider
from brain.providers.openai import OpenAIProvider


# ---------------------------------------------------------------------------
# ProviderResponse dataclass
# ---------------------------------------------------------------------------

class TestProviderResponse(unittest.TestCase):
    def test_required_field_only(self):
        r = ProviderResponse(content="hello")
        self.assertEqual(r.content, "hello")

    def test_default_values(self):
        r = ProviderResponse(content="hi")
        self.assertEqual(r.model, "")
        self.assertEqual(r.provider, "")
        self.assertEqual(r.tokens_used, 0)
        self.assertFalse(r.cached)
        self.assertEqual(r.metadata, {})

    def test_explicit_values(self):
        r = ProviderResponse(
            content="out",
            model="gpt-4o",
            provider="openai",
            tokens_used=42,
            cached=True,
            metadata={"k": "v"},
        )
        self.assertEqual(r.model, "gpt-4o")
        self.assertEqual(r.provider, "openai")
        self.assertEqual(r.tokens_used, 42)
        self.assertTrue(r.cached)
        self.assertEqual(r.metadata, {"k": "v"})

    def test_metadata_instances_are_independent(self):
        r1 = ProviderResponse(content="a")
        r2 = ProviderResponse(content="b")
        r1.metadata["x"] = 1
        self.assertNotIn("x", r2.metadata)


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------

class TestProviderErrors(unittest.TestCase):
    def test_provider_error_is_exception(self):
        self.assertTrue(issubclass(ProviderError, Exception))

    def test_auth_error_is_provider_error(self):
        self.assertTrue(issubclass(ProviderAuthError, ProviderError))

    def test_rate_limit_error_is_provider_error(self):
        self.assertTrue(issubclass(ProviderRateLimitError, ProviderError))

    def test_unavailable_error_is_provider_error(self):
        self.assertTrue(issubclass(ProviderUnavailableError, ProviderError))

    def test_can_catch_subclass_as_provider_error(self):
        with self.assertRaises(ProviderError):
            raise ProviderAuthError("bad key")


# ---------------------------------------------------------------------------
# ProviderConfig
# ---------------------------------------------------------------------------

class TestProviderConfig(unittest.TestCase):
    def test_all_defaults(self):
        cfg = ProviderConfig()
        self.assertEqual(cfg.type, "")
        self.assertEqual(cfg.model, "")
        self.assertEqual(cfg.api_key, "")
        self.assertEqual(cfg.base_url, "")
        self.assertEqual(cfg.timeout, 30)
        self.assertEqual(cfg.max_tokens, 1024)
        self.assertEqual(cfg.extra, {})

    def test_is_enabled_false_when_type_empty(self):
        self.assertFalse(ProviderConfig().is_enabled())

    def test_is_enabled_true_when_type_set(self):
        self.assertTrue(ProviderConfig(type="ollama").is_enabled())

    def test_explicit_fields(self):
        cfg = ProviderConfig(type="anthropic", model="claude-haiku-4-5", api_key="sk-x", timeout=60)
        self.assertEqual(cfg.type, "anthropic")
        self.assertEqual(cfg.model, "claude-haiku-4-5")
        self.assertEqual(cfg.api_key, "sk-x")
        self.assertEqual(cfg.timeout, 60)

    def test_extra_dict_instances_independent(self):
        c1 = ProviderConfig()
        c2 = ProviderConfig()
        c1.extra["foo"] = "bar"
        self.assertNotIn("foo", c2.extra)


# ---------------------------------------------------------------------------
# create_provider factory
# ---------------------------------------------------------------------------

class TestCreateProviderFactory(unittest.TestCase):
    def test_none_config_returns_none(self):
        self.assertIsNone(create_provider(None))

    def test_empty_type_returns_none(self):
        self.assertIsNone(create_provider(ProviderConfig()))

    def test_anthropic_creates_anthropic_provider(self):
        cfg = ProviderConfig(type="anthropic")
        provider = create_provider(cfg)
        self.assertIsInstance(provider, AnthropicProvider)

    def test_openai_creates_openai_provider(self):
        cfg = ProviderConfig(type="openai")
        provider = create_provider(cfg)
        self.assertIsInstance(provider, OpenAIProvider)

    def test_open_ai_alias(self):
        cfg = ProviderConfig(type="open_ai")
        provider = create_provider(cfg)
        self.assertIsInstance(provider, OpenAIProvider)

    def test_open_ai_hyphen_alias(self):
        cfg = ProviderConfig(type="open-ai")
        provider = create_provider(cfg)
        self.assertIsInstance(provider, OpenAIProvider)

    def test_ollama_creates_ollama_provider(self):
        cfg = ProviderConfig(type="ollama")
        provider = create_provider(cfg)
        self.assertIsInstance(provider, OllamaProvider)

    def test_unknown_type_raises_provider_error(self):
        cfg = ProviderConfig(type="cohere")
        with self.assertRaises(ProviderError):
            create_provider(cfg)


# ---------------------------------------------------------------------------
# AnthropicProvider
# ---------------------------------------------------------------------------

def _make_anthropic_response(content: str = "answer", model: str = "claude-sonnet-4-6") -> MagicMock:
    """Build a minimal fake anthropic.Message."""
    msg = MagicMock()
    msg.content = [MagicMock(text=content)]
    msg.model = model
    msg.usage.input_tokens = 10
    msg.usage.output_tokens = 5
    return msg


class TestAnthropicProvider(unittest.TestCase):
    def setUp(self):
        self.cfg = ProviderConfig(type="anthropic", api_key="sk-test")
        self.provider = AnthropicProvider(self.cfg)

    def test_name(self):
        self.assertEqual(self.provider.name, "anthropic")

    @patch.object(AnthropicProvider, "_call")
    def test_ask_returns_response(self, mock_call):
        mock_call.return_value = ProviderResponse(content="the answer", provider="anthropic")
        resp = self.provider.ask("What is X?")
        self.assertEqual(resp.content, "the answer")
        mock_call.assert_called_once()

    @patch.object(AnthropicProvider, "_call")
    def test_plan_passes_system(self, mock_call):
        mock_call.return_value = ProviderResponse(content="1. do this", provider="anthropic")
        resp = self.provider.plan("Build the feature")
        self.assertIn("do this", resp.content)
        args = mock_call.call_args
        # system prompt should be present (second positional arg)
        self.assertIsNotNone(args)

    @patch.object(AnthropicProvider, "_call")
    def test_summarize_returns_response(self, mock_call):
        mock_call.return_value = ProviderResponse(content="summary text", provider="anthropic")
        resp = self.provider.summarize("Long document content here...")
        self.assertEqual(resp.content, "summary text")

    @patch.object(AnthropicProvider, "_call")
    def test_review_without_criteria(self, mock_call):
        mock_call.return_value = ProviderResponse(content="looks good", provider="anthropic")
        resp = self.provider.review("some code")
        self.assertEqual(resp.content, "looks good")

    @patch.object(AnthropicProvider, "_call")
    def test_review_with_criteria(self, mock_call):
        mock_call.return_value = ProviderResponse(content="passes", provider="anthropic")
        resp = self.provider.review("code", criteria=["no globals", "docstrings"])
        self.assertEqual(resp.content, "passes")

    def test_auth_error_maps_to_provider_auth_error(self):
        import types
        mock_anthropic = types.ModuleType("anthropic")
        mock_anthropic.AuthenticationError = type("AuthenticationError", (Exception,), {})
        mock_anthropic.RateLimitError = type("RateLimitError", (Exception,), {})
        mock_anthropic.APIConnectionError = type("APIConnectionError", (Exception,), {})
        mock_anthropic.APIStatusError = type("APIStatusError", (Exception,), {})
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = mock_anthropic.AuthenticationError("bad key")
        mock_anthropic.Anthropic = MagicMock(return_value=mock_client)

        with patch.dict(sys.modules, {"anthropic": mock_anthropic}):
            with self.assertRaises(ProviderAuthError):
                self.provider._call([], "sys", 100)

    def test_rate_limit_maps_to_provider_rate_limit_error(self):
        import types
        mock_anthropic = types.ModuleType("anthropic")
        mock_anthropic.AuthenticationError = type("AuthenticationError", (Exception,), {})
        mock_anthropic.RateLimitError = type("RateLimitError", (Exception,), {})
        mock_anthropic.APIConnectionError = type("APIConnectionError", (Exception,), {})
        mock_anthropic.APIStatusError = type("APIStatusError", (Exception,), {})
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = mock_anthropic.RateLimitError("slow down")
        mock_anthropic.Anthropic = MagicMock(return_value=mock_client)

        with patch.dict(sys.modules, {"anthropic": mock_anthropic}):
            with self.assertRaises(ProviderRateLimitError):
                self.provider._call([], "sys", 100)


# ---------------------------------------------------------------------------
# OpenAIProvider
# ---------------------------------------------------------------------------

def _make_openai_response(content: str = "answer", model: str = "gpt-4o-mini") -> MagicMock:
    choice = MagicMock()
    choice.message.content = content
    resp = MagicMock()
    resp.choices = [choice]
    resp.model = model
    resp.usage.total_tokens = 20
    return resp


class TestOpenAIProvider(unittest.TestCase):
    def setUp(self):
        self.cfg = ProviderConfig(type="openai", api_key="sk-test")
        self.provider = OpenAIProvider(self.cfg)

    def test_name(self):
        self.assertEqual(self.provider.name, "openai")

    @patch.object(OpenAIProvider, "_call")
    def test_ask_returns_response(self, mock_call):
        mock_call.return_value = ProviderResponse(content="result", provider="openai")
        resp = self.provider.ask("question")
        self.assertEqual(resp.content, "result")

    @patch.object(OpenAIProvider, "_call")
    def test_plan_calls_through(self, mock_call):
        mock_call.return_value = ProviderResponse(content="plan steps", provider="openai")
        resp = self.provider.plan("do the thing")
        self.assertEqual(resp.content, "plan steps")

    @patch.object(OpenAIProvider, "_call")
    def test_summarize_returns_response(self, mock_call):
        mock_call.return_value = ProviderResponse(content="short", provider="openai")
        resp = self.provider.summarize("very long text")
        self.assertEqual(resp.content, "short")

    @patch.object(OpenAIProvider, "_call")
    def test_review_with_criteria(self, mock_call):
        mock_call.return_value = ProviderResponse(content="ok", provider="openai")
        resp = self.provider.review("content", criteria=["correctness"])
        self.assertEqual(resp.content, "ok")

    def test_auth_error_maps_correctly(self):
        import types
        mock_openai = types.ModuleType("openai")
        mock_openai.AuthenticationError = type("AuthenticationError", (Exception,), {})
        mock_openai.RateLimitError = type("RateLimitError", (Exception,), {})
        mock_openai.APIConnectionError = type("APIConnectionError", (Exception,), {})
        mock_openai.APIStatusError = type("APIStatusError", (Exception,), {})
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = mock_openai.AuthenticationError("bad key")
        mock_openai.OpenAI = MagicMock(return_value=mock_client)

        with patch.dict(sys.modules, {"openai": mock_openai}):
            with self.assertRaises(ProviderAuthError):
                self.provider._call([{"role": "user", "content": "x"}])

    def test_rate_limit_maps_correctly(self):
        import types
        mock_openai = types.ModuleType("openai")
        mock_openai.AuthenticationError = type("AuthenticationError", (Exception,), {})
        mock_openai.RateLimitError = type("RateLimitError", (Exception,), {})
        mock_openai.APIConnectionError = type("APIConnectionError", (Exception,), {})
        mock_openai.APIStatusError = type("APIStatusError", (Exception,), {})
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = mock_openai.RateLimitError("slow")
        mock_openai.OpenAI = MagicMock(return_value=mock_client)

        with patch.dict(sys.modules, {"openai": mock_openai}):
            with self.assertRaises(ProviderRateLimitError):
                self.provider._call([{"role": "user", "content": "x"}])

    def test_connection_error_maps_correctly(self):
        import types
        mock_openai = types.ModuleType("openai")
        mock_openai.AuthenticationError = type("AuthenticationError", (Exception,), {})
        mock_openai.RateLimitError = type("RateLimitError", (Exception,), {})
        mock_openai.APIConnectionError = type("APIConnectionError", (Exception,), {})
        mock_openai.APIStatusError = type("APIStatusError", (Exception,), {})
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = mock_openai.APIConnectionError("no conn")
        mock_openai.OpenAI = MagicMock(return_value=mock_client)

        with patch.dict(sys.modules, {"openai": mock_openai}):
            with self.assertRaises(ProviderUnavailableError):
                self.provider._call([{"role": "user", "content": "x"}])


# ---------------------------------------------------------------------------
# OllamaProvider
# ---------------------------------------------------------------------------

def _make_ollama_response_bytes(content: str = "ollama says hi") -> BytesIO:
    import json
    payload = {
        "message": {"role": "assistant", "content": content},
        "model": "llama3",
        "eval_count": 10,
        "prompt_eval_count": 5,
    }
    return BytesIO(json.dumps(payload).encode("utf-8"))


class TestOllamaProvider(unittest.TestCase):
    def setUp(self):
        self.cfg = ProviderConfig(type="ollama", model="llama3")
        self.provider = OllamaProvider(self.cfg)

    def test_name(self):
        self.assertEqual(self.provider.name, "ollama")

    @patch("brain.providers.ollama.urlopen")
    def test_ask_returns_response(self, mock_urlopen):
        mock_urlopen.return_value.__enter__ = lambda s: s
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value.read.return_value = _make_ollama_response_bytes("the answer").read()
        resp = self.provider.ask("What is 2+2?")
        self.assertEqual(resp.content, "the answer")
        self.assertEqual(resp.provider, "ollama")

    @patch("brain.providers.ollama.urlopen")
    def test_ask_includes_context(self, mock_urlopen):
        import json
        captured: list[dict] = []

        def fake_urlopen(req, timeout=None):
            payload = json.loads(req.data.decode())
            captured.append(payload)
            ctx_mgr = MagicMock()
            ctx_mgr.__enter__ = lambda s: s
            ctx_mgr.__exit__ = MagicMock(return_value=False)
            ctx_mgr.read.return_value = _make_ollama_response_bytes("ok").read()
            return ctx_mgr

        mock_urlopen.side_effect = fake_urlopen
        self.provider.ask("question", context="some context")
        msg_content = captured[0]["messages"][0]["content"]
        self.assertIn("some context", msg_content)
        self.assertIn("question", msg_content)

    @patch("brain.providers.ollama.urlopen")
    def test_plan_prepends_system_text(self, mock_urlopen):
        import json
        captured: list[dict] = []

        def fake_urlopen(req, timeout=None):
            captured.append(json.loads(req.data.decode()))
            ctx_mgr = MagicMock()
            ctx_mgr.__enter__ = lambda s: s
            ctx_mgr.__exit__ = MagicMock(return_value=False)
            ctx_mgr.read.return_value = _make_ollama_response_bytes("plan").read()
            return ctx_mgr

        mock_urlopen.side_effect = fake_urlopen
        self.provider.plan("implement the feature")
        msg_content = captured[0]["messages"][0]["content"]
        self.assertIn("engineering planning", msg_content.lower())

    @patch("brain.providers.ollama.urlopen")
    def test_summarize_returns_response(self, mock_urlopen):
        mock_urlopen.return_value.__enter__ = lambda s: s
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value.read.return_value = _make_ollama_response_bytes("brief").read()
        resp = self.provider.summarize("A very long document...")
        self.assertEqual(resp.content, "brief")

    @patch("brain.providers.ollama.urlopen")
    def test_review_with_criteria(self, mock_urlopen):
        import json
        captured: list[dict] = []

        def fake_urlopen(req, timeout=None):
            captured.append(json.loads(req.data.decode()))
            ctx_mgr = MagicMock()
            ctx_mgr.__enter__ = lambda s: s
            ctx_mgr.__exit__ = MagicMock(return_value=False)
            ctx_mgr.read.return_value = _make_ollama_response_bytes("reviewed").read()
            return ctx_mgr

        mock_urlopen.side_effect = fake_urlopen
        self.provider.review("content", criteria=["no bugs"])
        msg_content = captured[0]["messages"][0]["content"]
        self.assertIn("no bugs", msg_content)

    @patch("brain.providers.ollama.urlopen")
    def test_review_without_criteria(self, mock_urlopen):
        mock_urlopen.return_value.__enter__ = lambda s: s
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value.read.return_value = _make_ollama_response_bytes("ok").read()
        resp = self.provider.review("some content")
        self.assertEqual(resp.content, "ok")

    @patch("brain.providers.ollama.urlopen")
    def test_http_error_raises_provider_error(self, mock_urlopen):
        from urllib.error import HTTPError
        mock_urlopen.side_effect = HTTPError(
            url="http://localhost:11434/api/chat",
            code=500,
            msg="Internal Server Error",
            hdrs=None,  # type: ignore[arg-type]
            fp=None,  # type: ignore[arg-type]
        )
        with self.assertRaises(ProviderError):
            self.provider.ask("q")

    @patch("brain.providers.ollama.urlopen")
    def test_url_error_raises_provider_unavailable_error(self, mock_urlopen):
        from urllib.error import URLError
        mock_urlopen.side_effect = URLError("Connection refused")
        with self.assertRaises(ProviderUnavailableError):
            self.provider.ask("q")

    @patch("brain.providers.ollama.urlopen")
    def test_token_count_from_eval_fields(self, mock_urlopen):
        import json
        payload = {
            "message": {"role": "assistant", "content": "hi"},
            "model": "llama3",
            "eval_count": 10,
            "prompt_eval_count": 7,
        }
        mock_urlopen.return_value.__enter__ = lambda s: s
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value.read.return_value = json.dumps(payload).encode()
        resp = self.provider.ask("token test")
        self.assertEqual(resp.tokens_used, 17)


# ---------------------------------------------------------------------------
# MondayConfig — provider_config field
# ---------------------------------------------------------------------------

class TestMondayConfigProviderField(unittest.TestCase):
    def test_default_is_none(self):
        from monday.config import MondayConfig
        cfg = MondayConfig()
        self.assertIsNone(cfg.provider_config)

    def test_can_set_provider_config(self):
        from monday.config import MondayConfig
        pc = ProviderConfig(type="ollama")
        cfg = MondayConfig(provider_config=pc)
        self.assertIs(cfg.provider_config, pc)

    def test_provider_config_is_enabled(self):
        from monday.config import MondayConfig
        cfg = MondayConfig(provider_config=ProviderConfig(type="anthropic"))
        self.assertTrue(cfg.provider_config.is_enabled())  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# AdvisorEngine — AI enrichment
# ---------------------------------------------------------------------------

class TestAdvisorAIEnrichment(unittest.TestCase):
    def _make_engine(self, provider=None):
        from pathlib import Path
        from advisor.engine import AdvisorEngine

        monday = MagicMock()
        engine = AdvisorEngine(monday=monday, project_root=Path("."), provider=provider)
        return engine

    def _make_advisory(self, sprint_goal: str = "do work", confidence: float = 0.55):
        from advisor.advisory import Advisory
        return Advisory(
            sprint_goal=sprint_goal,
            confidence=confidence,
            repository_summary="A test project with 5 KB entries.",
            data_sources=["doctor"],
        )

    def test_no_provider_advisory_unchanged(self):
        engine = self._make_engine(provider=None)
        advisory = self._make_advisory()
        engine._enrich_advisory_with_ai(advisory)  # called manually; with None provider analyze() skips it
        # Nothing should change because it's guarded by `if self._provider is not None`
        # The method is harmless to call but the real guard is in analyze().
        # Verify the method itself with explicit None provider does nothing abnormal
        self.assertEqual(advisory.sprint_goal, "do work")

    def test_with_provider_sprint_goal_updated(self):
        mock_provider = MagicMock()
        mock_provider.name = "ollama"
        mock_provider.ask.return_value = ProviderResponse(content="Build the authentication module")
        engine = self._make_engine(provider=mock_provider)
        advisory = self._make_advisory(sprint_goal="Continue forward momentum")
        engine._enrich_advisory_with_ai(advisory)
        self.assertEqual(advisory.sprint_goal, "Build the authentication module")

    def test_with_provider_confidence_raised(self):
        mock_provider = MagicMock()
        mock_provider.name = "anthropic"
        mock_provider.ask.return_value = ProviderResponse(content="Ship it")
        engine = self._make_engine(provider=mock_provider)
        advisory = self._make_advisory(confidence=0.55)
        engine._enrich_advisory_with_ai(advisory)
        self.assertGreater(advisory.confidence, 0.55)
        self.assertLessEqual(advisory.confidence, 0.95)

    def test_confidence_capped_at_0_95(self):
        mock_provider = MagicMock()
        mock_provider.name = "anthropic"
        mock_provider.ask.return_value = ProviderResponse(content="Enrich")
        engine = self._make_engine(provider=mock_provider)
        advisory = self._make_advisory(confidence=0.90)
        engine._enrich_advisory_with_ai(advisory)
        self.assertLessEqual(advisory.confidence, 0.95)

    def test_with_provider_data_sources_updated(self):
        mock_provider = MagicMock()
        mock_provider.name = "openai"
        mock_provider.ask.return_value = ProviderResponse(content="Plan X")
        engine = self._make_engine(provider=mock_provider)
        advisory = self._make_advisory()
        engine._enrich_advisory_with_ai(advisory)
        self.assertIn("openai", advisory.data_sources)

    def test_provider_failure_does_not_propagate(self):
        mock_provider = MagicMock()
        mock_provider.name = "anthropic"
        mock_provider.ask.side_effect = ProviderUnavailableError("down")
        engine = self._make_engine(provider=mock_provider)
        advisory = self._make_advisory(sprint_goal="original goal")
        engine._enrich_advisory_with_ai(advisory)
        # Should not raise; original goal unchanged after failure
        self.assertEqual(advisory.sprint_goal, "original goal")

    def test_provider_name_not_duplicated_in_data_sources(self):
        mock_provider = MagicMock()
        mock_provider.name = "ollama"
        mock_provider.ask.return_value = ProviderResponse(content="ok")
        engine = self._make_engine(provider=mock_provider)
        advisory = self._make_advisory()
        advisory.data_sources.append("ollama")  # pre-existing
        engine._enrich_advisory_with_ai(advisory)
        self.assertEqual(advisory.data_sources.count("ollama"), 1)


if __name__ == "__main__":
    unittest.main()
