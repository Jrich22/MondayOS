"""
Role definitions for the MondayOS Multi-Agent Runtime.

A *role* is what work is routed to — not a specific model or person. Each role
is pure data: a slug, a human title, a description of responsibilities, a default
AI provider, the capabilities it advertises, and the gated actions it would need
a human to approve. Tasks are assigned and run against a role; the registry then
resolves the role to a concrete agent (and thus a provider).

Adding a new role is a single entry in ``ROLES`` — no core logic changes. This is
what satisfies the "future agents can be added without changing core logic"
requirement at the role level.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# The universe of actions that always require explicit human approval before an
# agent may perform them. Kept here (imported by agents.gates) so the policy and
# the role definitions share one source of truth. See docs/APPROVAL_GATES.md.
# "publish_content" is the Growth Bot's outbound action (ADR-012). It is named for
# content specifically because Monday.publish() already means Confluence *document*
# publishing, which is a separate concern and separately gated.
GATED_ACTIONS: frozenset[str] = frozenset(
    {"commit", "push", "secrets", "live_trade", "destructive", "publish_content"}
)


@dataclass(frozen=True)
class Role:
    """
    A role the runtime can route work to.

    Attributes:
        slug:             Stable identifier used on the CLI and in storage
                          (e.g. "lead-engineer").
        title:            Human-readable name (e.g. "Lead Engineer").
        description:      What this role is responsible for.
        default_provider: AI provider a freshly-seeded agent for this role uses
                          ("anthropic" | "openai" | "ollama"). Overridable per
                          agent at registration time.
        capabilities:     What the role advertises it can do (documentation +
                          routing hints; not an authorization list).
        gated_actions:    Subset of GATED_ACTIONS this role would typically need
                          escalated. Documented policy — the actual block happens
                          per-run in agents.gates against the requested actions.
    """

    slug: str
    title: str
    description: str
    default_provider: str
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    gated_actions: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# The six roles. Adding one here is the ONLY change needed to support it.
#
# Provider defaults: cpo → openai (ChatGPT) and lead-engineer → anthropic
# (Claude Code) are pinned by the initiative. qa / security / reviewer default
# to anthropic; research → openai. Every default is overridable via
# `monday agent register --provider …`.
# ---------------------------------------------------------------------------
ROLES: dict[str, Role] = {
    "cpo": Role(
        slug="cpo",
        title="CPO",
        description=(
            "Product strategy, roadmap, acceptance criteria, and prioritization. "
            "Turns goals into well-formed, ranked work."
        ),
        default_provider="openai",
        capabilities=("strategy", "roadmap", "acceptance-criteria", "prioritization"),
        gated_actions=(),
    ),
    "lead-engineer": Role(
        slug="lead-engineer",
        title="Lead Engineer",
        description=(
            "Implementation, code changes, and architecture execution. Produces "
            "the plan and the diff; committing/pushing stays behind approval gates."
        ),
        default_provider="anthropic",
        capabilities=("implementation", "code-changes", "architecture", "refactoring"),
        gated_actions=("commit", "push"),
    ),
    "qa": Role(
        slug="qa",
        title="QA",
        description=(
            "Tests, regression, validation, coverage, and bug reproduction. "
            "Runs independently to verify other agents' work."
        ),
        default_provider="anthropic",
        capabilities=("testing", "regression", "validation", "coverage", "bug-repro"),
        gated_actions=(),
    ),
    "security": Role(
        slug="security",
        title="Security",
        description=(
            "Secrets and credential review, risky-diff analysis, live-trading "
            "safety, and dependency risk. Advisory by default; escalates writes."
        ),
        default_provider="anthropic",
        capabilities=(
            "secrets-review",
            "credential-audit",
            "risk-assessment",
            "dependency-audit",
            "live-trading-safety",
        ),
        gated_actions=("secrets",),
    ),
    "research": Role(
        slug="research",
        title="Research",
        description=(
            "Data analysis, experiment design, research reports, and edge "
            "discovery. Read-only investigation that produces knowledge."
        ),
        default_provider="openai",
        capabilities=("data-analysis", "experiment-design", "research-reports", "edge-discovery"),
        gated_actions=(),
    ),
    "reviewer": Role(
        slug="reviewer",
        title="Reviewer",
        description=(
            "Code review, PR review, and risk assessment. The independent second "
            "opinion before work is approved."
        ),
        default_provider="anthropic",
        capabilities=("code-review", "pr-review", "risk-assessment"),
        gated_actions=(),
    ),
}


# Convenience view: role slug → default provider. Derived from ROLES so there is
# still only one source of truth. Change a default by editing the Role above.
DEFAULT_ROLE_PROVIDERS: dict[str, str] = {
    slug: role.default_provider for slug, role in ROLES.items()
}


class UnknownRoleError(ValueError):
    """Raised when a role slug is not defined in ROLES."""


def normalize_role(slug: str) -> str:
    """Lower-case and hyphenate a role slug (accepts 'Lead Engineer', 'lead_engineer')."""
    return (slug or "").strip().lower().replace("_", "-").replace(" ", "-")


def get_role(slug: str) -> Role:
    """
    Return the Role for ``slug``.

    Raises UnknownRoleError (with the valid list) if the slug is not defined.
    """
    normalized = normalize_role(slug)
    if normalized not in ROLES:
        valid = sorted(ROLES)
        raise UnknownRoleError(f"Unknown role {slug!r}. Valid roles: {valid}")
    return ROLES[normalized]


def list_roles() -> list[Role]:
    """Return all roles, ordered by slug."""
    return [ROLES[slug] for slug in sorted(ROLES)]
