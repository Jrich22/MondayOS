"""
Tests for provider resolution from the environment.

The property that matters most here is negative: **a key must never travel
through MondayOS's own config object**. Each provider reads its own variable
directly, so the secret cannot reach a repr, a log line, or a crash dump by way
of `ProviderConfig` — and that is only true as long as nothing puts it there.

The rest is precedence: an exported value beats a file, an explicit choice beats
detection, and a provider that cannot run is reported rather than silently
swapped for one that can.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from monday.provider_env import (
    ProviderChoice,
    choose,
    load_env_file,
    ollama_available,
    ollama_has_model,
    provider_config,
)

SECRET = "sk-ant-test-0123456789abcdefghijklmnop"


class TestLoadEnvFile(unittest.TestCase):
    def test_loads_names_and_values(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(f'ANTHROPIC_API_KEY="{SECRET}"\nMONDAYOS_ANTHROPIC_MODEL=claude-x\n')
            env: dict[str, str] = {}
            loaded = load_env_file(path, env)
            self.assertEqual(loaded, ["ANTHROPIC_API_KEY", "MONDAYOS_ANTHROPIC_MODEL"])
            self.assertEqual(env["ANTHROPIC_API_KEY"], SECRET)

    def test_an_exported_value_is_never_overridden(self):
        """A shell export is a deliberate act; a file is a default."""
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(f"ANTHROPIC_API_KEY={SECRET}\n")
            env = {"ANTHROPIC_API_KEY": "already-set-in-shell"}
            loaded = load_env_file(path, env)
            self.assertEqual(loaded, [])
            self.assertEqual(env["ANTHROPIC_API_KEY"], "already-set-in-shell")

    def test_it_returns_names_only_never_values(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(f"ANTHROPIC_API_KEY={SECRET}\n")
            loaded = load_env_file(path, {})
            self.assertNotIn(SECRET, " ".join(loaded))

    def test_placeholders_and_blanks_are_skipped(self):
        """A placeholder makes a provider look configured and fail on first call."""
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "ANTHROPIC_API_KEY=your-key-here\nOPENAI_API_KEY=\nOLLAMA_HOST=<host>\n"
            )
            self.assertEqual(load_env_file(path, {}), [])

    def test_comments_exports_and_quotes_are_handled(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("# a comment\nexport OPENAI_API_KEY='quoted-value'\n\n")
            env: dict[str, str] = {}
            load_env_file(path, env)
            self.assertEqual(env["OPENAI_API_KEY"], "quoted-value")

    def test_a_missing_file_is_not_an_error(self):
        self.assertEqual(load_env_file(Path("/nonexistent/.env"), {}), [])


class TestChoose(unittest.TestCase):
    def test_anthropic_wins_when_its_key_is_present(self):
        choice = choose({"ANTHROPIC_API_KEY": SECRET})
        self.assertEqual(choice.provider, "anthropic")
        self.assertIn("ANTHROPIC_API_KEY", choice.reason)

    def test_openai_is_used_when_anthropic_is_absent(self):
        choice = choose({"OPENAI_API_KEY": SECRET})
        self.assertEqual(choice.provider, "openai")

    def test_an_explicit_request_is_honoured_over_detection(self):
        """
        Being told "you asked for openai" beats silently getting anthropic.

        A caller who set MONDAYOS_PROVIDER has a reason; overriding it because
        another key happens to be present would answer with a model they did not
        choose and did not expect to pay for.
        """
        choice = choose({"MONDAYOS_PROVIDER": "openai", "ANTHROPIC_API_KEY": SECRET})
        self.assertEqual(choice.provider, "openai")
        self.assertIn("MONDAYOS_PROVIDER", choice.reason)

    def test_the_model_override_is_read(self):
        choice = choose({"ANTHROPIC_API_KEY": SECRET, "MONDAYOS_ANTHROPIC_MODEL": "claude-x"})
        self.assertEqual(choice.model, "claude-x")

    def test_no_usable_provider_reports_what_to_set(self):
        """
        Whatever the machine running this has, an unusable result must be actionable.

        There are now three outcomes rather than two: no provider at all, a
        daemon that cannot serve the requested model (RC1/ACC-001), or a working
        provider. The first two say different things because they have different
        fixes, so this asserts the property they share -- the reason names a
        concrete next step and points at the documentation -- instead of assuming
        which branch a given machine lands in.
        """
        choice = choose({"MONDAYOS_PROVIDER": ""})
        if not choice.configured:
            self.assertIn("PROVIDERS.md", choice.reason)
            actionable = "ANTHROPIC_API_KEY" in choice.reason or "ollama pull" in choice.reason
            self.assertTrue(actionable, choice.reason)

    def test_describe_never_contains_a_key(self):
        text = choose({"ANTHROPIC_API_KEY": SECRET}).describe()
        self.assertNotIn(SECRET, text)
        self.assertIn("anthropic", text)

    def test_describe_states_whether_streaming_is_real(self):
        """Honesty about streaming is a stated requirement, not a nicety."""
        streaming = ProviderChoice("anthropic", "claude-x", "because", streams=True)
        plain = ProviderChoice("ollama", "llama3", "because", streams=False)
        self.assertIn("streams natively", streaming.describe())
        self.assertIn("no incremental streaming", plain.describe())


class TestProviderConfig(unittest.TestCase):
    def test_the_key_never_enters_the_config_object(self):
        """
        The load-bearing assertion of this module.

        Providers read their own variable from the environment. If the key were
        copied into ProviderConfig it could surface in a repr, a log line or a
        crash dump — none of which anyone would think to check.
        """
        config = provider_config({"ANTHROPIC_API_KEY": SECRET})
        self.assertIsNotNone(config)
        self.assertEqual(config.api_key, "")
        self.assertNotIn(SECRET, repr(config))

    def test_it_carries_provider_and_model_only(self):
        config = provider_config(
            {"ANTHROPIC_API_KEY": SECRET, "MONDAYOS_ANTHROPIC_MODEL": "claude-x"}
        )
        self.assertEqual(config.type, "anthropic")
        self.assertEqual(config.model, "claude-x")

    def test_none_when_nothing_is_available(self):
        choice = choose({"MONDAYOS_PROVIDER": ""})
        if not choice.configured:
            self.assertIsNone(provider_config({"MONDAYOS_PROVIDER": ""}))


class TestStreamingHonesty(unittest.TestCase):
    def test_anthropic_declares_real_streaming(self):
        from brain.providers.anthropic import AnthropicProvider
        from brain.providers.factory import ProviderConfig

        self.assertTrue(AnthropicProvider(ProviderConfig(type="anthropic")).supports_streaming)

    def test_a_provider_without_streaming_says_so(self):
        """
        The fallback must not pretend.

        A provider that has not implemented `stream` still works — it yields the
        whole answer as one chunk — but it reports supports_streaming=False so
        callers can show the difference rather than animating a single chunk to
        look like tokens arriving.
        """
        from brain.providers.factory import ProviderConfig
        from brain.providers.ollama import OllamaProvider

        provider = OllamaProvider(ProviderConfig(type="ollama"))
        self.assertFalse(provider.supports_streaming)

    def test_the_fallback_still_produces_a_terminating_stream(self):
        from brain.providers.base import AIProvider, ProviderAvailability, ProviderResponse

        class Plain(AIProvider):
            @property
            def name(self) -> str:
                return "plain"

            def availability(self) -> ProviderAvailability:
                return ProviderAvailability(available=True, provider="plain")

            def ask(self, prompt, context="", max_tokens=1024, **kwargs):
                return ProviderResponse(content="whole answer", model="p-1", provider="plain")

            def plan(self, objective, context="", max_tokens=2048, **kwargs):
                return self.ask(objective)

            def summarize(self, content, max_words=150, **kwargs):
                return self.ask(content)

            def review(self, content, criteria="", **kwargs):
                return self.ask(content)

        chunks = list(Plain().stream("q"))
        self.assertEqual(chunks[0].text, "whole answer")
        self.assertTrue(chunks[-1].done)
        self.assertEqual(chunks[-1].model, "p-1")


if __name__ == "__main__":
    unittest.main()


class TestOllamaModelValidation(unittest.TestCase):
    """
    RC1/ACC-001. A provider that reports ready must be able to answer.

    A responding Ollama daemon used to be enough to report `configured`. It is
    not: the daemon answers `/api/tags` whether or not the model MondayOS intends
    to use has been pulled. A default install with any other model set reported
    ready and then returned HTTP 404 on every generation — thirteen consecutive
    turns of the S5 acceptance run failed this way while selection insisted the
    provider was fine.

    This module already refuses a placeholder API key for precisely this reason,
    and its own comment says why: such a value "makes a provider look configured
    and fail at the first call". The model name now meets the same standard.
    """

    def test_a_bare_name_matches_any_tag(self):
        """`llama3.1` means the default tag of that model."""
        self.assertTrue(ollama_has_model("llama3.1", ["llama3.1:8b"]))
        self.assertTrue(ollama_has_model("qwen2.5-coder", ["qwen2.5-coder:14b"]))

    def test_an_explicit_tag_must_match_exactly(self):
        """`llama3.1:8b` and `llama3.1:70b` are not interchangeable."""
        self.assertTrue(ollama_has_model("llama3.1:8b", ["llama3.1:8b"]))
        self.assertFalse(ollama_has_model("llama3.1:70b", ["llama3.1:8b"]))

    def test_a_missing_model_does_not_match(self):
        self.assertFalse(ollama_has_model("llama3", ["llama3.1:8b", "qwen2.5-coder:14b"]))
        self.assertFalse(ollama_has_model("", ["llama3"]))

    def test_a_daemon_without_the_model_is_not_configured(self):
        """The exact S5 failure: daemon up, `llama3` absent, every call 404s."""
        with patch("monday.provider_env.ollama_models", return_value=["llama3.1:8b"]):
            choice = choose({"MONDAYOS_PROVIDER": ""})
        self.assertFalse(choice.configured)

    def test_the_reason_names_the_model_and_the_command_that_fixes_it(self):
        """
        An error a user can act on without reading the source.

        "No provider configured" would be true and useless. The message has to
        say which model is missing, how to install it, and what is already there.
        """
        with patch("monday.provider_env.ollama_models", return_value=["llama3.1:8b"]):
            reason = choose({"MONDAYOS_PROVIDER": ""}).reason
        self.assertIn("llama3", reason)
        self.assertIn("ollama pull", reason)
        self.assertIn("MONDAYOS_OLLAMA_MODEL", reason)
        self.assertIn("llama3.1:8b", reason)

    def test_a_daemon_with_the_model_is_configured(self):
        with patch("monday.provider_env.ollama_models", return_value=["llama3.1:8b"]):
            choice = choose({"MONDAYOS_PROVIDER": "", "MONDAYOS_OLLAMA_MODEL": "llama3.1:8b"})
        self.assertTrue(choice.configured)
        self.assertEqual(choice.provider, "ollama")

    def test_a_daemon_with_nothing_installed_says_so(self):
        with patch("monday.provider_env.ollama_models", return_value=[]):
            reason = choose({"MONDAYOS_PROVIDER": ""}).reason
        self.assertIn("no models installed", reason)
        self.assertIn("ollama pull", reason)

    def test_no_daemon_falls_through_to_the_unconfigured_message(self):
        """A daemon that is not running is a different problem with a different fix."""
        with patch("monday.provider_env.ollama_models", return_value=None):
            reason = choose({"MONDAYOS_PROVIDER": ""}).reason
        self.assertIn("ANTHROPIC_API_KEY", reason)

    def test_availability_still_reports_only_whether_the_daemon_answers(self):
        with patch("monday.provider_env.ollama_models", return_value=[]):
            self.assertTrue(ollama_available())
        with patch("monday.provider_env.ollama_models", return_value=None):
            self.assertFalse(ollama_available())

    def test_a_hosted_provider_is_unaffected(self):
        """This check belongs to Ollama alone."""
        choice = choose({"ANTHROPIC_API_KEY": SECRET})
        self.assertEqual(choice.provider, "anthropic")
