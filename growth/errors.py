"""Typed error classes for the growth module."""

from __future__ import annotations


class GrowthError(Exception):
    """Base class for all growth module errors."""


class InvalidProjectSlugError(GrowthError):
    """Raised when a project name cannot be normalized to a safe workspace slug."""

    def __init__(self, name: str, reason: str) -> None:
        self.name = name
        self.reason = reason
        super().__init__(f"Invalid project {name!r}: {reason}")


class ProjectNotRegisteredError(GrowthError):
    """Raised when a growth workspace is requested for an unregistered project."""

    def __init__(self, name: str, registered: list[str]) -> None:
        self.name = name
        self.registered = registered
        known = ", ".join(sorted(registered)) if registered else "none"
        super().__init__(
            f"Project {name!r} is not registered with MondayOS. Registered: {known}. "
            "Register it with `monday project register` before creating a growth workspace."
        )


class AmbiguousProjectError(GrowthError):
    """Raised when two registered project names share a slug but not a source path."""

    def __init__(self, slug: str, names: list[str]) -> None:
        self.slug = slug
        self.names = names
        super().__init__(
            f"Registered projects {sorted(names)!r} both normalize to slug {slug!r} but point at "
            "different source paths. Growth cannot choose between them; rename or remove one."
        )


class WorkspaceNotFoundError(GrowthError):
    """Raised when a growth workspace has not been initialized for a project."""

    def __init__(self, slug: str) -> None:
        self.slug = slug
        super().__init__(
            f"No growth workspace for project {slug!r}. Create one with "
            f"`monday growth workspace-init --project {slug}`."
        )


class WorkspaceExistsError(GrowthError):
    """Raised when initializing a workspace that already exists."""

    def __init__(self, slug: str) -> None:
        self.slug = slug
        super().__init__(f"A growth workspace for project {slug!r} already exists.")


class ContentNotFoundError(GrowthError):
    """Raised when a content item id cannot be resolved within a workspace."""

    def __init__(self, content_id: str, slug: str) -> None:
        self.content_id = content_id
        self.slug = slug
        super().__init__(f"No content item {content_id!r} in growth workspace {slug!r}.")


class InvalidTransitionError(GrowthError):
    """Raised when a content item is moved along an illegal lifecycle edge."""

    def __init__(self, from_status: str, to_status: str) -> None:
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(
            f"Illegal content transition {from_status} -> {to_status}. "
            "See docs/GROWTH_BOT.md for the lifecycle."
        )


class BindingNotFoundError(GrowthError):
    """Raised when a platform binding is not present in a workspace."""

    def __init__(self, platform: str, slug: str) -> None:
        self.platform = platform
        self.slug = slug
        super().__init__(f"No {platform!r} binding in growth workspace {slug!r}.")


class GrowthParseError(GrowthError):
    """Raised when a stored growth file cannot be parsed."""

    def __init__(self, message: str, source_path: str = "<string>") -> None:
        self.source_path = source_path
        super().__init__(f"{source_path}: {message}")


class CampaignNotFoundError(GrowthError):
    """Raised when a campaign id cannot be resolved within a workspace."""

    def __init__(self, campaign_id: str, slug: str) -> None:
        self.campaign_id = campaign_id
        self.slug = slug
        super().__init__(f"No campaign {campaign_id!r} in growth workspace {slug!r}.")


class CrossCampaignError(GrowthError):
    """Raised when content is assigned to a campaign outside its own workspace."""

    def __init__(self, content_id: str, campaign_id: str, slug: str) -> None:
        self.content_id = content_id
        self.campaign_id = campaign_id
        self.slug = slug
        super().__init__(
            f"Cannot assign {content_id!r} to campaign {campaign_id!r}: the campaign is not in "
            f"workspace {slug!r}. Content never moves between projects or across workspaces."
        )


class OnboardingIncompleteError(GrowthError):
    """Raised when an operation requires onboarding that has not been completed."""

    def __init__(self, slug: str, missing: list[str]) -> None:
        self.slug = slug
        self.missing = missing
        super().__init__(
            f"Growth onboarding for {slug!r} is incomplete. Missing: {', '.join(missing)}."
        )
