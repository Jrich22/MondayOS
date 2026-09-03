"""
Resolving which AI provider MondayOS should use, from the environment.

MondayOS has always had `ProviderConfig` and `create_provider`; what it did not
have was anything that *filled them in* at startup. `MondayConfig.provider_config`
defaults to None, so a process that never set it explicitly ran with no provider
at all — which is why the AI Workspace could hold a conversation but never answer
one.

This module is that missing step and nothing more. It introduces no new secrets
system: it reads the same environment variables `docs/PROVIDERS.md` already
documents, and it never stores, logs or returns a key.

Selection order, when `MONDAYOS_PROVIDER` is not set:

    1. anthropic   ANTHROPIC_API_KEY present and the SDK importable
    2. openai      OPENAI_API_KEY present and the SDK importable
    3. ollama      a local daemon answering on OLLAMA_HOST
    4. none        no provider; the workspace says so rather than pretending

Hosted providers come first because they are the capable ones, and local last
because it is the fallback that always works. The order is fixed and stated so
that "which model answered this?" has an answer you can predict rather than
discover.
"""

from __future__ import annotations

import os
import re
import urllib.error
import urllib.request
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The variable a provider needs, and the model override for it. Both names are
# the ones already in docs/PROVIDERS.md.
PROVIDER_ENV: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "ollama": "",
}
MODEL_ENV: dict[str, str] = {
    "anthropic": "MONDAYOS_ANTHROPIC_MODEL",
    "openai": "MONDAYOS_OPENAI_MODEL",
    "ollama": "MONDAYOS_OLLAMA_MODEL",
}

DEFAULT_OLLAMA_HOST = "http://localhost:11434"

_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


@dataclass(frozen=True)
class ProviderChoice:
    """Which provider was selected, and why — with no key in sight."""

    provider: str
    model: str
    reason: str
    streams: bool = False

    @property
    def configured(self) -> bool:
        return bool(self.provider)

    def describe(self) -> str:
        """A line safe to print, log, or show in a UI."""
        if not self.configured:
            return f"No AI provider configured — {self.reason}"
        streaming = "streams natively" if self.streams else "no incremental streaming"
        return f"{self.provider} · {self.model} · {streaming} — {self.reason}"


def load_env_file(path: Path, environ: MutableMapping[str, str] | None = None) -> list[str]:
    """
    Load `KEY=value` lines from a dotenv-style file into the environment.

    **Never overrides an existing variable.** A value already exported in the
    shell is a deliberate act; a file is a default. Reversing that precedence
    would make it possible to be running with a key you cannot see in your own
    environment.

    Returns the names it set — names only. Values are never returned, logged or
    echoed, and this function is the only thing in MondayOS that reads the file.

    No new dependency: `python-dotenv` is not installed here and one env file is
    not worth adding one for.
    """
    env = environ if environ is not None else os.environ
    loaded: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return loaded

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ENV_LINE.match(stripped)
        if not match:
            continue
        name, raw = match.group(1), match.group(2).strip()
        if raw and raw[0] == raw[-1] and raw[0] in ("'", '"'):
            raw = raw[1:-1]
        # An empty or obviously-placeholder value is worse than nothing: it makes
        # a provider look configured and fail at the first call.
        if not raw or raw.startswith(("your-", "<", "changeme")):
            continue
        if name in env:
            continue
        env[name] = raw
        loaded.append(name)
    return loaded


def ollama_available(host: str = "", timeout: float = 1.0) -> bool:
    """Whether a local Ollama daemon is answering. Never raises."""
    base = host or os.environ.get("OLLAMA_HOST", DEFAULT_OLLAMA_HOST)
    if not base.startswith("http"):
        base = f"http://{base}"
    try:
        with urllib.request.urlopen(f"{base.rstrip('/')}/api/tags", timeout=timeout) as response:
            return bool(200 <= response.status < 300)
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _sdk_available(module: str) -> bool:
    try:
        __import__(module)
    except ImportError:
        return False
    return True


def choose(environ: Mapping[str, str] | None = None) -> ProviderChoice:
    """
    Which provider to use, from the environment alone.

    An explicit `MONDAYOS_PROVIDER` is honoured even when it cannot run: being
    told "you asked for anthropic and ANTHROPIC_API_KEY is not set" is more
    useful than silently getting a different model than you asked for.
    """
    env = environ if environ is not None else os.environ
    requested = (env.get("MONDAYOS_PROVIDER") or "").strip().lower()

    if requested:
        return _describe(requested, env, reason=f"MONDAYOS_PROVIDER={requested}")

    if env.get("ANTHROPIC_API_KEY") and _sdk_available("anthropic"):
        return _describe("anthropic", env, reason="ANTHROPIC_API_KEY is set")
    if env.get("OPENAI_API_KEY") and _sdk_available("openai"):
        return _describe("openai", env, reason="OPENAI_API_KEY is set")
    if ollama_available():
        return _describe("ollama", env, reason="a local Ollama daemon is responding")

    return ProviderChoice(
        provider="",
        model="",
        reason=(
            "set ANTHROPIC_API_KEY or OPENAI_API_KEY, or run a local Ollama daemon. "
            "See docs/PROVIDERS.md."
        ),
    )


def _describe(provider: str, env: Mapping[str, str], reason: str) -> ProviderChoice:
    from brain.providers.factory import ProviderConfig, create_provider

    model = (env.get(MODEL_ENV.get(provider, ""), "") or "").strip()
    # Ask the provider itself whether it streams rather than keeping a table
    # here: a second list of capabilities is a second thing to keep true.
    streams = False
    try:
        built = create_provider(ProviderConfig(type=provider, model=model))
        if built is not None:
            streams = built.supports_streaming
            model = model or getattr(built, "_model", "") or ""
    except Exception:  # noqa: BLE001 — reporting a choice must never fail
        pass
    return ProviderChoice(provider=provider, model=model, reason=reason, streams=streams)


def provider_config(environ: Mapping[str, str] | None = None) -> Any:
    """
    A ProviderConfig for the chosen provider, or None when none is available.

    The key is left out deliberately: every provider reads its own variable from
    the environment, so the secret never passes through MondayOS's own config
    object — and therefore cannot end up in a repr, a log line, or a crash dump.
    """
    from brain.providers.factory import ProviderConfig

    choice = choose(environ)
    if not choice.configured:
        return None
    return ProviderConfig(type=choice.provider, model=choice.model)
