"""
Platform bindings — how a growth workspace names a publishing account (ADR-011).

A binding is a *reference*: the platform, the account it publishes as, and the
NAME of the environment variable holding the credential. The credential itself is
never stored, never serialized, and never returned by any method here. It resolves
at publish time and is otherwise absent from the workspace entirely.

This mirrors the contract integrations/confluence/config.py already establishes for
Confluence credentials — from_env / credential_check / redacted — so MondayOS has
one way of handling secrets rather than two.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

# The platforms a workspace can bind. Extending this list is the only change needed
# to support another network; nothing in the binding logic is platform-specific.
SUPPORTED_PLATFORMS: tuple[str, ...] = (
    "linkedin",
    "x",
    "instagram",
    "facebook",
    "tiktok",
    "youtube",
    "threads",
)

_SECRET_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")


class UnsupportedPlatformError(ValueError):
    """Raised when a binding names a platform growth does not support."""

    def __init__(self, platform: str) -> None:
        self.platform = platform
        super().__init__(
            f"Unsupported platform {platform!r}. Supported: {', '.join(SUPPORTED_PLATFORMS)}"
        )


class InvalidSecretNameError(ValueError):
    """Raised when a secret reference is not a plausible environment variable name."""

    def __init__(self, secret_name: str) -> None:
        self.secret_name = secret_name
        super().__init__(
            f"Secret reference {secret_name!r} is not a valid environment variable name "
            "(expected UPPER_SNAKE_CASE). Store the credential in the environment and "
            "reference it by name — never paste the credential itself."
        )


@dataclass
class CredentialCheck:
    """Whether a binding's credential is actually present in the environment."""

    ok: bool
    missing: list[str] = field(default_factory=list)

    def instructions(self) -> str:
        """Human-readable guidance for resolving a failed check."""
        if self.ok:
            return "Credential present."
        names = ", ".join(self.missing)
        return f"Missing credential(s) in the environment: {names}. Export them and retry."


@dataclass
class PlatformBinding:
    """
    A workspace's binding to one publishing account on one platform.

    Attributes:
        platform:     One of SUPPORTED_PLATFORMS.
        account_id:   Stable platform-side account identifier. Fingerprinted, because
                      approval is of a specific account.
        account_handle: Display handle. Not fingerprinted — a handle rename is not a
                      change to which account publishes.
        secret_name:  Environment variable holding the credential. Never the value.
        status:       "active" or "disabled".
    """

    platform: str
    account_id: str
    account_handle: str = ""
    secret_name: str = ""
    status: str = "active"

    def __post_init__(self) -> None:
        self.platform = normalize_platform(self.platform)
        if self.secret_name and not _SECRET_NAME_RE.match(self.secret_name):
            raise InvalidSecretNameError(self.secret_name)

    def credential_check(self, environ: dict[str, str] | None = None) -> CredentialCheck:
        """Report whether this binding's credential is present, without revealing it."""
        env = environ if environ is not None else dict(os.environ)
        if not self.secret_name:
            return CredentialCheck(ok=False, missing=["<no secret_name configured>"])
        if not env.get(self.secret_name, "").strip():
            return CredentialCheck(ok=False, missing=[self.secret_name])
        return CredentialCheck(ok=True)

    def redacted(self) -> dict[str, str]:
        """A representation safe for logs, prompts, and API responses."""
        return {
            "platform": self.platform,
            "account_id": self.account_id,
            "account_handle": self.account_handle,
            "secret_name": self.secret_name,
            "status": self.status,
        }

    def to_dict(self) -> dict[str, str]:
        """Serialise for storage. Identical to redacted() — there is nothing to hide."""
        return self.redacted()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlatformBinding:
        return cls(
            platform=str(data.get("platform", "")),
            account_id=str(data.get("account_id", "")),
            account_handle=str(data.get("account_handle", "")),
            secret_name=str(data.get("secret_name", "")),
            status=str(data.get("status", "active")),
        )


def normalize_platform(platform: str) -> str:
    """Normalize and validate a platform name. Raises UnsupportedPlatformError."""
    candidate = (platform or "").strip().lower()
    if candidate not in SUPPORTED_PLATFORMS:
        raise UnsupportedPlatformError(platform)
    return candidate
