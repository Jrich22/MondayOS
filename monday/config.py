"""Configuration for the Monday public API."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from brain.providers.factory import ProviderConfig


@dataclass
class MondayConfig:
    """
    Configuration for a Monday instance.

    All fields have sensible defaults so `Monday()` works with no arguments.
    In production, construct from an explicit config file rather than relying
    on defaults (see TODO below).

    Attributes:
        project_root:           Root directory of the MondayOS project.
        model_tier:             Default model tier for task execution.
                                One of: "high", "standard", "fast", "local".
        require_human_approval: If True, production-impacting actions pause
                                for human approval before executing.
        log_level:              Logging verbosity. One of: "DEBUG", "INFO",
                                "WARN", "ERROR".
        session_id:             Explicit session ID. Auto-generated if None.
        provider_config:        Optional AI provider configuration. When set,
                                the Monday instance will use the specified AI
                                provider to enrich advisory recommendations.
                                None (default) preserves fully deterministic
                                behaviour with no external calls.

    TODO: Add from_toml(path) classmethod to load from config/monday.toml.
    TODO: Add from_env() classmethod to override fields from environment vars.
    TODO: Add model API key validation (keys are loaded from env, not stored here).
    """

    project_root: Path = Path(".")
    model_tier: str = "standard"
    require_human_approval: bool = True
    log_level: str = "INFO"
    session_id: str | None = None
    provider_config: "ProviderConfig | None" = None
