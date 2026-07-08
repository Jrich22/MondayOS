"""
Confluence connection configuration and credential checks.

Credentials are read from the environment only — never stored in source or
persisted to disk. MondayOS stays the system of record; Confluence is a
publishing destination configured through four environment variables:

    CONFLUENCE_BASE_URL    e.g. https://your-org.atlassian.net
    CONFLUENCE_EMAIL       the Atlassian account email
    CONFLUENCE_API_TOKEN   an API token (id.atlassian.com/manage/api-tokens)
    CONFLUENCE_SPACE_KEY   the default space to publish into

A fifth, optional variable routes publishing to an in-memory fake client for
local demos and tests — it never touches the network:

    MONDAYOS_CONFLUENCE_FAKE=1
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

BASE_URL_ENV = "CONFLUENCE_BASE_URL"
EMAIL_ENV = "CONFLUENCE_EMAIL"
API_TOKEN_ENV = "CONFLUENCE_API_TOKEN"
SPACE_KEY_ENV = "CONFLUENCE_SPACE_KEY"
FAKE_ENV = "MONDAYOS_CONFLUENCE_FAKE"


@dataclass
class CredentialCheck:
    """
    Whether Confluence credentials are present, and if not, how to fix them.

    Only environment-variable *names* are reported — token values are never
    included in the check or its message.
    """

    ok: bool
    missing: list[str] = field(default_factory=list)
    reason: str = ""

    def instructions(self) -> str:
        """A single actionable line for the user."""
        if self.ok:
            return "Confluence credentials are configured."
        return (
            "Missing Confluence credentials: "
            + ", ".join(self.missing)
            + ". Set them in the environment (never in source): "
            + "CONFLUENCE_BASE_URL, CONFLUENCE_EMAIL, CONFLUENCE_API_TOKEN, CONFLUENCE_SPACE_KEY."
        )


@dataclass
class ConfluenceConfig:
    """
    Resolved Confluence connection settings.

    Build with :meth:`from_env`; do not construct with literal secrets in code.
    """

    base_url: str = ""
    email: str = ""
    api_token: str = ""
    space_key: str = ""
    use_fake: bool = False

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "ConfluenceConfig":
        """Read connection settings from the environment (os.environ by default)."""
        env = environ if environ is not None else os.environ
        return cls(
            base_url=env.get(BASE_URL_ENV, "").rstrip("/"),
            email=env.get(EMAIL_ENV, ""),
            api_token=env.get(API_TOKEN_ENV, ""),
            space_key=env.get(SPACE_KEY_ENV, ""),
            use_fake=_truthy(env.get(FAKE_ENV, "")),
        )

    def credential_check(self, require_space: bool = True) -> CredentialCheck:
        """
        Report whether the credentials needed to publish are present.

        The fake client needs no credentials. Otherwise base URL, email and API
        token are always required; the space key is required unless the caller
        is updating a known page (``require_space=False``).
        """
        if self.use_fake:
            return CredentialCheck(ok=True, reason="fake client (no network)")

        checks = [
            (BASE_URL_ENV, self.base_url, True),
            (EMAIL_ENV, self.email, True),
            (API_TOKEN_ENV, self.api_token, True),
            (SPACE_KEY_ENV, self.space_key, require_space),
        ]
        missing = [name for name, value, required in checks if required and not value]
        ok = not missing
        return CredentialCheck(
            ok=ok, missing=missing, reason="ready" if ok else "missing credentials"
        )

    def redacted(self) -> dict[str, str]:
        """A safe-to-log view — the API token is never exposed."""
        return {
            "base_url": self.base_url,
            "email": self.email,
            "api_token": "***set***" if self.api_token else "",
            "space_key": self.space_key,
            "use_fake": str(self.use_fake).lower(),
        }


def _truthy(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")
