"""
Synthetic demo data for a Growth workspace.

Everything this module writes is fake and is labelled as such at rest. Each
record it creates carries ``metadata["synthetic"] = True`` and a
``metadata["synthetic_source"]`` naming this module, so a later analytics or
Growth Brain pass can exclude it - or report on it as demo data - rather than
mistaking a seeded number for a measured one.

The seeding is deterministic: same inputs, same output, no clock and no random
source. A demo that differs between runs cannot be used as a test fixture, and
this module is used as both.

It creates a workspace, a brand and audience, an onboarding record, campaigns,
and content. It creates no publications, no metrics, and no platform
connections: publishing runs through the normal dispatcher against the fake
connector, and metrics arrive in increment 5.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from growth.campaign import CampaignStatus
from growth.content import ContentType
from growth.onboarding import PlatformIntent, WeeklyReview
from growth.store import WorkspaceHandle

# Marks every record this module writes. Checked by tests and available to any
# consumer that needs to exclude demo data from a real total.
SYNTHETIC_FLAG = "synthetic"
SYNTHETIC_SOURCE = "growth.demo"

# A fixed epoch so seeded timestamps are reproducible across runs and machines.
DEMO_EPOCH = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


def synthetic_metadata(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """The marker every seeded record carries."""
    payload: dict[str, Any] = {SYNTHETIC_FLAG: True, "synthetic_source": SYNTHETIC_SOURCE}
    if extra:
        payload.update(extra)
    return payload


def is_synthetic(record: dict[str, Any]) -> bool:
    """True when a serialized record was produced by demo seeding."""
    meta = record.get("metadata")
    return bool(isinstance(meta, dict) and meta.get(SYNTHETIC_FLAG))


def seed_workspace(handle: WorkspaceHandle, epoch: datetime = DEMO_EPOCH) -> dict[str, Any]:
    """
    Populate a workspace with deterministic demo content.

    Returns a summary of what was created. Safe to call on an initialized
    workspace; it adds records rather than resetting anything.
    """
    workspace = handle.read()

    workspace.business.name = workspace.business.name or "Northwind Talent"
    workspace.business.description = "AI-native recruiting operations for scaling teams."
    workspace.business.industry = "HR technology"
    workspace.business.website = "https://example.com"
    workspace.business.products = ["Sourcing copilot", "Pipeline analytics"]
    workspace.business.competitors = ["Northwind Cloud"]

    workspace.brand.voice = "Direct, evidence-led, allergic to hype."
    workspace.brand.tone = "Confident and plain-spoken."
    workspace.brand.style_rules = [
        "No exclamation marks.",
        "Never claim a number we cannot show.",
        "Name the trade-off, not just the benefit.",
    ]
    workspace.brand.logos = ["assets/logo-primary.svg"]
    workspace.brand.approved_imagery = ["assets/team-01.png", "assets/product-02.png"]

    workspace.audience.icps = ["Series B-D talent teams"]
    workspace.audience.personas = ["VP Talent", "Head of Recruiting"]
    workspace.audience.job_titles = ["VP Talent", "Recruiting Lead", "Talent Partner"]
    workspace.audience.pain_points = [
        "Re-sourcing people they already evaluated",
        "No defensible record of hiring judgement",
    ]

    workspace.marketing.objectives = ["Generate 50 qualified demo requests per quarter"]
    workspace.marketing.kpis = ["demo_requests", "qualified_leads"]
    workspace.marketing.content_pillars = ["sourcing craft", "hiring data", "customer proof"]
    workspace.marketing.ctas = ["Book a demo", "Read the teardown"]

    workspace.onboarding.platform_intents = [
        PlatformIntent(platform="linkedin", account_label="Northwind Talent (company page)"),
        PlatformIntent(platform="x", account_label="@northwindtalent"),
    ]
    workspace.onboarding.cadence_per_week = 4
    workspace.onboarding.prohibited_content = [
        "Competitor names in a negative claim",
        "Unverified hiring statistics",
        "Candidate names or identifying details",
    ]
    workspace.onboarding.weekly_review = WeeklyReview(weekday="sunday", hour_utc=17)
    workspace.metadata.update(synthetic_metadata())
    handle.write(workspace)

    launch = handle.create_campaign(
        name="Shortlist-first launch",
        description="Introduce shortlist-first sourcing to talent leaders.",
        objective="Generate 50 demo requests",
        target_audience="VP Talent at Series B-D companies",
        primary_conversion_goal="demo_request",
        theme="sourcing craft",
        channels=["linkedin", "x"],
        cta="Book a demo",
        destination="https://example.com/demo",
        kpis=["demo_requests", "ctr"],
        start_date=epoch,
        end_date=epoch + timedelta(days=28),
    )
    launch.metadata.update(synthetic_metadata())
    handle.save_campaign(launch)
    handle.transition_campaign(launch.id, CampaignStatus.ACTIVE, reason="demo seed")

    education = handle.create_campaign(
        name="Hiring data teardowns",
        description="Evergreen educational series on hiring data.",
        objective="Build authority with talent leaders",
        target_audience="Recruiting leads",
        primary_conversion_goal="newsletter_signup",
        theme="hiring data",
        channels=["linkedin"],
        cta="Read the teardown",
        destination="https://example.com/teardowns",
        kpis=["engagement_rate"],
    )
    education.metadata.update(synthetic_metadata())
    handle.save_campaign(education)

    created: list[str] = []

    # One idea, two platform variants. They are separate items sharing a group id
    # because approval binds one platform and one copy (ADR-013).
    variant_group = "vg-shortlist-launch"
    linkedin = handle.create_content(
        platform="linkedin",
        account="demo-linkedin-account",
        copy=(
            "Most sourcing tools optimise for filling this requisition. "
            "When the role closes, the judgement evaporates.\n\n"
            "Shortlist-first sourcing keeps the person as the durable unit, so the "
            "reasoning compounds across every req."
        ),
        cta="Book a demo",
        destination_url="https://example.com/demo",
        campaign=launch.id,
        expected_goal="12 demo requests",
        expected_audience="VP Talent",
        scheduled_at=epoch,
        content_type=ContentType.SOCIAL_POST,
        title="Shortlist-first sourcing, explained",
        themes=["sourcing craft"],
        audience="VP Talent",
        variant_group_id=variant_group,
        reuse_eligible=True,
    )
    created.append(linkedin.id)

    x_variant = handle.create_content(
        platform="x",
        account="demo-x-account",
        copy=(
            "Sourcing tools key candidates to a requisition. "
            "Close the role and the judgement disappears with it.\n\n"
            "Store the person once. The reasoning compounds."
        ),
        cta="Book a demo",
        destination_url="https://example.com/demo",
        campaign=launch.id,
        expected_goal="4 demo requests",
        expected_audience="Recruiting leads",
        scheduled_at=epoch + timedelta(hours=3),
        content_type=ContentType.SOCIAL_POST,
        title="Shortlist-first sourcing, explained",
        themes=["sourcing craft"],
        audience="Recruiting leads",
        variant_group_id=variant_group,
        reuse_eligible=True,
    )
    created.append(x_variant.id)

    carousel = handle.create_content(
        platform="linkedin",
        account="demo-linkedin-account",
        copy="Five numbers every talent team should be able to answer on demand.",
        cta="Read the teardown",
        destination_url="https://example.com/teardowns",
        campaign=education.id,
        expected_goal="200 newsletter signups",
        expected_audience="Recruiting leads",
        scheduled_at=epoch + timedelta(days=2),
        content_type=ContentType.CAROUSEL,
        title="Five hiring numbers",
        themes=["hiring data"],
        audience="Recruiting leads",
        reuse_eligible=True,
    )
    created.append(carousel.id)

    story = handle.create_content(
        platform="linkedin",
        account="demo-linkedin-account",
        copy="How one talent team cut re-sourcing by keeping their judgement.",
        cta="Book a demo",
        destination_url="https://example.com/demo",
        campaign=launch.id,
        expected_goal="8 demo requests",
        expected_audience="VP Talent",
        scheduled_at=epoch + timedelta(days=4),
        content_type=ContentType.EDUCATIONAL_POST,
        title="Customer story: keeping judgement",
        themes=["customer proof"],
        audience="VP Talent",
    )
    created.append(story.id)

    for content_id in created:
        item = handle.get_content(content_id)
        item.metadata.update(synthetic_metadata())
        handle.save_content(item)

    return {
        "project": handle.slug,
        "synthetic": True,
        "campaigns": [launch.id, education.id],
        "content": created,
        "variant_groups": [variant_group],
        "note": (
            "All seeded records are marked synthetic in their metadata. "
            "No publications, metrics, or platform connections were created."
        ),
    }
