"""
Tests for model-backed content generation (TASK-0067, model seam).

The model is the one place in growth/ that talks to a provider, so these tests
point at what makes that safe: it sees one workspace, its output is validated
and fails closed, the safety gate runs on what it actually produced rather than
on what it was told, and nothing silently falls back to templates.

No live provider is used anywhere. The fake below implements the MondayOS
AIProvider interface, so the seam under test is the real one.
"""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from brain.providers.base import (
    AIProvider,
    ProviderAvailability,
    ProviderError,
    ProviderResponse,
)
from growth import GrowthStore
from growth.campaign import CampaignStatus
from growth.content import ContentStatus
from growth.generation import (
    AssetKind,
    BrandContext,
    ContentPlanner,
    Copywriter,
    MissingBrandContextError,
    ModelContentWriter,
    ModelGenerationError,
    ModelOutputInvalidError,
    PlannedPost,
    TemplateContentWriter,
    WeeklyPackageBuilder,
    build_prompt,
    gate_for_review,
    make_variants,
    parse_response,
)
from growth.service import GrowthService
from monday.project import ProjectRegistry

T0 = datetime(2026, 9, 7, 9, 0, tzinfo=UTC)


class FakeContentProvider(AIProvider):
    """
    Offline AIProvider returning a scripted response. No network, no SDK.

    Implements the real interface, so tests exercise the actual seam rather than
    a stand-in for it.
    """

    def __init__(
        self,
        response: str | None = None,
        name: str = "fake-writer",
        model: str = "fake-writer-1",
        available: bool = True,
        raises: Exception | None = None,
    ) -> None:
        self._name = name
        self._model = model
        self._available = available
        self._raises = raises
        self._response = (
            response
            if response is not None
            else json.dumps(
                {
                    "title": "Shortlist-first sourcing",
                    "body": "Talent teams keep re-sourcing people they already evaluated.",
                    "cta": "Book a demo",
                    "themes": ["sourcing craft"],
                    "warnings": [],
                }
            )
        )
        self.prompts: list[str] = []

    @property
    def name(self) -> str:
        return self._name

    def availability(self) -> ProviderAvailability:
        return ProviderAvailability(
            available=self._available,
            provider=self._name,
            model=self._model,
            reason="ready" if self._available else "no API key configured",
            env_var="" if self._available else "FAKE_WRITER_KEY",
        )

    def _respond(self, prompt: str) -> ProviderResponse:
        self.prompts.append(prompt)
        if self._raises is not None:
            raise self._raises
        return ProviderResponse(content=self._response, model=self._model, provider=self._name)

    def ask(self, prompt: str, context: str = "", max_tokens: int = 1024, **kw: Any):
        return self._respond(prompt)

    def plan(self, objective: str, context: str = "", max_tokens: int = 2048, **kw: Any):
        return self._respond(objective)

    def review(self, content: str, criteria: str = "", max_tokens: int = 1024, **kw: Any):
        return self._respond(content)

    def summarize(self, text: str, max_tokens: int = 512, **kw: Any):
        return self._respond(text)


def _root(tmp: str, projects: dict[str, str] | None = None) -> Path:
    root = Path(tmp)
    (root / "config").mkdir(parents=True, exist_ok=True)
    registry = ProjectRegistry(root / "config")
    for name, source in (projects or {"acme": "acme-src"}).items():
        (root / source).mkdir(parents=True, exist_ok=True)
        registry.register(name, root / source, overwrite=True)
    return root


def _brand(**kw) -> BrandContext:
    fields = {
        "project": "acme",
        "voice": "Direct, evidence-led.",
        "audience": "VP Talent",
        "personas": ("VP Talent",),
        "pain_points": ("re-sourcing people",),
        "objective": "Generate 50 demo requests",
        "content_pillars": ("sourcing craft",),
        "ctas": ("Book a demo",),
        "prohibited": ("Unverified hiring statistics",),
        "products": ("Sourcing copilot",),
        "website": "https://example.com",
        "approved_assets": ("assets/logo.svg",),
    }
    fields.update(kw)
    return BrandContext(**fields)


def _planned(**kw) -> PlannedPost:
    fields = {
        "slot": 1,
        "platform": "linkedin",
        "kind": AssetKind.LINKEDIN_POST,
        "campaign": "CAMPAIGN-0001",
        "theme": "sourcing craft",
        "scheduled_at": T0,
        "goal": "demo_request",
        "cta": "Book a demo",
        "recommendation_ids": ["REC-abc"],
        "experiment_ids": ["EXP-xyz"],
        "rationale": "Addresses REC-abc.",
    }
    fields.update(kw)
    return PlannedPost(**fields)


# ---------------------------------------------------------------------------
# The provider seam
# ---------------------------------------------------------------------------


class TestProviderSeam(unittest.TestCase):
    def test_growth_imports_no_vendor_sdk(self):
        """growth/ consumes the abstraction, never anthropic or openai directly."""
        import subprocess

        hits = subprocess.run(
            ["grep", "-rnE", "--include=*.py", r"^(import|from) (anthropic|openai)\b", "growth/"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parent.parent,
        ).stdout.strip()
        self.assertEqual(hits, "", f"growth/ imports a vendor SDK directly: {hits}")

    def test_no_vendor_name_in_generation_logic(self):
        """
        Scoped to *.py on purpose.

        A .pyc embeds its own absolute source path, so an unscoped grep matches
        whatever the checkout happens to live under and the guardrail passes or
        fails on directory name rather than on code.
        """
        import subprocess

        hits = subprocess.run(
            [
                "grep",
                "-rniE",
                "--include=*.py",
                r"claude|gpt-|anthropic|openai",
                "growth/generation/",
            ],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parent.parent,
        ).stdout.strip()
        self.assertEqual(hits, "", f"vendor-specific logic in generation: {hits}")

    def test_the_brain_remains_model_free(self):
        import subprocess

        hits = subprocess.run(
            [
                "grep",
                "-rnE",
                "--include=*.py",
                r"AIProvider|anthropic|openai|^(import|from) (requests|httpx|urllib|socket)",
                "growth/brain/",
            ],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parent.parent,
        ).stdout.strip()
        self.assertEqual(hits, "", f"the Brain reaches a model: {hits}")

    def test_the_writer_satisfies_the_content_writer_protocol(self):
        writer = ModelContentWriter(FakeContentProvider())
        title, body = writer.write(_planned(), _brand(), angle="")
        self.assertTrue(title)
        self.assertTrue(body)

    def test_the_rest_of_generation_does_not_know_which_writer_ran(self):
        for writer in (TemplateContentWriter(), ModelContentWriter(FakeContentProvider())):
            with self.subTest(writer=type(writer).__name__):
                asset = Copywriter(writer).draft(_planned(), _brand())
                self.assertTrue(asset.draft)
                self.assertEqual(asset.recommendation_ids, ["REC-abc"])


# ---------------------------------------------------------------------------
# Workspace isolation at the prompt boundary
# ---------------------------------------------------------------------------


class TestPromptIsolation(unittest.TestCase):
    def test_the_prompt_contains_only_the_current_workspace(self):
        prompt = build_prompt(_planned(), _brand(project="alpha"), angle="")
        self.assertIn("alpha", prompt)
        self.assertIn("Sourcing copilot", prompt)
        self.assertNotIn("beta", prompt.lower().replace("beta-", ""))

    def test_another_projects_content_never_reaches_the_prompt(self):
        with TemporaryDirectory() as tmp:
            root = _root(tmp, {"alpha": "a", "beta": "b"})
            store = GrowthStore(root)
            store.init_workspace("alpha")
            store.init_workspace("beta")
            beta = store.open("beta")
            beta.create_content(
                platform="linkedin",
                copy="BETA CONFIDENTIAL LAUNCH COPY",
                title="Beta secret",
                themes=["beta-only-theme"],
            )
            workspace = beta.read()
            workspace.brand.voice = "BETA BRAND VOICE"
            beta.write(workspace)

            provider = FakeContentProvider()
            from growth.generation import brand_context_for

            alpha_brand = brand_context_for(store.open("alpha"))
            alpha_brand = _brand(project="alpha")
            Copywriter(ModelContentWriter(provider)).draft(_planned(), alpha_brand)

            self.assertEqual(len(provider.prompts), 1)
            sent = provider.prompts[0]
            for leaked in (
                "BETA CONFIDENTIAL",
                "Beta secret",
                "beta-only-theme",
                "BETA BRAND VOICE",
            ):
                self.assertNotIn(leaked, sent, f"{leaked!r} leaked into the prompt")

    def test_the_prompt_carries_the_brain_evidence(self):
        prompt = build_prompt(_planned(), _brand(), angle="")
        self.assertIn("REC-abc", prompt)
        self.assertIn("EXP-xyz", prompt)
        self.assertIn("Growth Brain", prompt)

    def test_the_prompt_states_the_projects_restrictions(self):
        prompt = build_prompt(_planned(), _brand(), angle="")
        self.assertIn("Unverified hiring statistics", prompt)
        self.assertIn("testimonials", prompt)


# ---------------------------------------------------------------------------
# Structured output, failing closed
# ---------------------------------------------------------------------------


class TestStructuredOutput(unittest.TestCase):
    def test_a_valid_object_parses(self):
        payload = parse_response(json.dumps({"title": "t", "body": "b", "cta": "c"}))
        self.assertEqual(payload["title"], "t")

    def test_a_fenced_object_parses(self):
        raw = '```json\n{"title": "t", "body": "b", "cta": "c"}\n```'
        self.assertEqual(parse_response(raw)["body"], "b")

    def test_prose_fails_closed(self):
        with self.assertRaises(ModelOutputInvalidError):
            parse_response("Here is a great LinkedIn post about sourcing!")

    def test_malformed_json_fails_closed(self):
        with self.assertRaises(ModelOutputInvalidError):
            parse_response('{"title": "t", "body": ')

    def test_an_empty_response_fails_closed(self):
        with self.assertRaises(ModelOutputInvalidError):
            parse_response("")

    def test_a_missing_required_field_fails_closed(self):
        with self.assertRaises(ModelOutputInvalidError) as ctx:
            parse_response(json.dumps({"title": "t", "body": "b"}))
        self.assertIn("cta", str(ctx.exception))

    def test_an_empty_required_field_fails_closed(self):
        with self.assertRaises(ModelOutputInvalidError):
            parse_response(json.dumps({"title": "t", "body": "   ", "cta": "c"}))

    def test_a_runaway_body_fails_closed(self):
        with self.assertRaises(ModelOutputInvalidError):
            parse_response(json.dumps({"title": "t", "body": "x" * 9000, "cta": "c"}))

    def test_fields_outside_the_schema_are_dropped(self):
        payload = parse_response(
            json.dumps({"title": "t", "body": "b", "cta": "c", "invented": "x"})
        )
        self.assertNotIn("invented", payload)

    def test_a_writer_receiving_prose_fails_closed(self):
        writer = ModelContentWriter(FakeContentProvider(response="Just some prose."))
        with self.assertRaises(ModelOutputInvalidError):
            writer.write(_planned(), _brand(), angle="")


# ---------------------------------------------------------------------------
# Safety runs on output, not on the prompt
# ---------------------------------------------------------------------------


class TestSafetyAfterGeneration(unittest.TestCase):
    def test_a_model_fabricating_a_statistic_is_caught_post_generation(self):
        """The prompt forbids it; the gate is what actually enforces it."""
        rogue = FakeContentProvider(
            response=json.dumps(
                {
                    "title": "Proof it works",
                    "body": "94% of customers cut time-to-hire in half.",
                    "cta": "Book a demo",
                }
            )
        )
        asset = Copywriter(ModelContentWriter(rogue)).draft(_planned(), _brand())
        self.assertTrue(asset.is_blocked)
        allowed, reasons = gate_for_review(asset, _brand())
        self.assertFalse(allowed)
        self.assertTrue(any("fabricated-statistic" in r for r in reasons))

    def test_a_model_fabricating_a_testimonial_is_caught(self):
        rogue = FakeContentProvider(
            response=json.dumps(
                {
                    "title": "What they say",
                    "body": 'Customers say: "this changed our hiring".',
                    "cta": "Book a demo",
                }
            )
        )
        asset = Copywriter(ModelContentWriter(rogue)).draft(_planned(), _brand())
        self.assertTrue(asset.is_blocked)

    def test_a_model_breaching_a_project_prohibition_is_caught(self):
        rogue = FakeContentProvider(
            response=json.dumps(
                {
                    "title": "Numbers",
                    "body": "Here are some Unverified hiring statistics for you.",
                    "cta": "Book a demo",
                }
            )
        )
        asset = Copywriter(ModelContentWriter(rogue)).draft(_planned(), _brand())
        self.assertTrue(asset.is_blocked)

    def test_a_model_touching_a_sensitive_category_escalates(self):
        rogue = FakeContentProvider(
            response=json.dumps(
                {
                    "title": "Compliance",
                    "body": "Our approach helps with GDPR compliance obligations.",
                    "cta": "Book a demo",
                }
            )
        )
        asset = Copywriter(ModelContentWriter(rogue)).draft(_planned(), _brand())
        self.assertIn("legal", asset.escalations)

    def test_clean_model_output_passes_the_gate(self):
        asset = Copywriter(ModelContentWriter(FakeContentProvider())).draft(_planned(), _brand())
        self.assertFalse(asset.is_blocked)
        self.assertTrue(gate_for_review(asset, _brand())[0])


# ---------------------------------------------------------------------------
# Brand context, provenance, no silent fallback
# ---------------------------------------------------------------------------


class TestContextProvenanceAndFallback(unittest.TestCase):
    def test_missing_brand_context_still_refuses_under_the_model_writer(self):
        writer = ModelContentWriter(FakeContentProvider())
        with self.assertRaises(MissingBrandContextError):
            writer.write(_planned(), _brand(voice=""), angle="")

    def test_the_provider_is_not_called_when_brand_context_is_missing(self):
        provider = FakeContentProvider()
        with self.assertRaises(MissingBrandContextError):
            ModelContentWriter(provider).write(_planned(), _brand(objective=""), angle="")
        self.assertEqual(provider.prompts, [])

    def test_an_unavailable_provider_surfaces_cleanly(self):
        writer = ModelContentWriter(FakeContentProvider(available=False))
        with self.assertRaises(ModelGenerationError) as ctx:
            writer.write(_planned(), _brand(), angle="")
        self.assertIn("FAKE_WRITER_KEY", str(ctx.exception))
        self.assertIn("No template fallback", str(ctx.exception))

    def test_a_provider_error_surfaces_cleanly(self):
        writer = ModelContentWriter(FakeContentProvider(raises=ProviderError("upstream exploded")))
        with self.assertRaises(ModelGenerationError) as ctx:
            writer.write(_planned(), _brand(), angle="")
        self.assertIn("upstream exploded", str(ctx.exception))

    def test_an_unexpected_provider_exception_still_surfaces(self):
        writer = ModelContentWriter(FakeContentProvider(raises=RuntimeError("boom")))
        with self.assertRaises(ModelGenerationError):
            writer.write(_planned(), _brand(), angle="")

    def test_model_mode_never_silently_becomes_template(self):
        """A failed model draft raises; it does not quietly return template copy."""
        writer = ModelContentWriter(FakeContentProvider(available=False))
        with self.assertRaises(ModelGenerationError):
            Copywriter(writer).draft(_planned(), _brand())

    def test_generation_method_is_recorded(self):
        model_asset = Copywriter(ModelContentWriter(FakeContentProvider())).draft(
            _planned(), _brand()
        )
        template_asset = Copywriter(TemplateContentWriter()).draft(_planned(), _brand())
        self.assertEqual(model_asset.generation_method, "model")
        self.assertEqual(model_asset.provider, "fake-writer")
        self.assertEqual(template_asset.generation_method, "template")
        self.assertEqual(template_asset.provider, "")

    def test_provenance_survives_into_the_payload(self):
        asset = Copywriter(ModelContentWriter(FakeContentProvider())).draft(_planned(), _brand())
        payload = asset.to_dict()
        self.assertEqual(payload["generation_method"], "model")
        self.assertEqual(payload["provider"], "fake-writer")
        self.assertEqual(payload["recommendation_ids"], ["REC-abc"])
        self.assertEqual(payload["experiment_ids"], ["EXP-xyz"])
        self.assertEqual(payload["campaign"], "CAMPAIGN-0001")

    def test_the_template_writer_remains_deterministic(self):
        writer = TemplateContentWriter()
        first = writer.write(_planned(), _brand(), angle="")
        second = writer.write(_planned(), _brand(), angle="")
        self.assertEqual(first, second)

    def test_provider_identity_is_not_part_of_the_fingerprint(self):
        """Approval is about the exact output, not about which model wrote it."""
        from growth.content import ContentItem

        item = ContentItem(
            id="CONTENT-0001",
            project="acme",
            platform="linkedin",
            account="a",
            copy="body",
            cta="Book a demo",
            destination_url="https://example.com",
        )
        before = item.current_fingerprint()
        item.metadata.update({"generation_method": "model", "provider": "fake-writer"})
        self.assertEqual(item.current_fingerprint(), before)


# ---------------------------------------------------------------------------
# Variants and the weekly package under model generation
# ---------------------------------------------------------------------------


class TestModelGenerationInThePackage(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = _root(self._tmp.name)
        self.store = GrowthStore(self.root)
        self.store.init_workspace("acme")
        self.handle = self.store.open("acme")
        campaign = self.handle.create_campaign(
            name="Launch",
            objective="Generate 50 demos",
            primary_conversion_goal="demo_request",
            cta="Book a demo",
            theme="sourcing craft",
        )
        self.handle.transition_campaign(campaign.id, CampaignStatus.ACTIVE)
        self.campaign = self.handle.get_campaign(campaign.id)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _build(self, provider: FakeContentProvider, platforms: list[str], multi: bool = False):
        plan = ContentPlanner("acme").plan_week(
            week_start=T0,
            cadence=len(platforms),
            recommendations=[],
            campaigns=[{**self.campaign.to_dict(), "accepts_content": True}],
            platforms=platforms,
        )
        builder = WeeklyPackageBuilder(self.handle, Copywriter(ModelContentWriter(provider)))
        return builder.build(
            brand=_brand(),
            plan=plan,
            recommendations=[],
            experiments=[],
            now=T0,
            multi_platform=multi,
        )

    def test_model_drafts_become_draft_content_items(self):
        package = self._build(FakeContentProvider(), ["linkedin"])
        for post in package.posts:
            item = self.handle.get_content(post.content_id)
            self.assertIs(item.status, ContentStatus.DRAFT)
            self.assertEqual(item.metadata["generation_method"], "model")
            self.assertEqual(item.metadata["provider"], "fake-writer")

    def test_variants_remain_independently_approvable(self):
        package = self._build(FakeContentProvider(), ["linkedin", "x"], multi=True)
        groups: dict[str, list[str]] = {}
        for post in package.posts:
            groups.setdefault(post.variant_group_id, []).append(post.content_id)
        group = next(ids for ids in groups.values() if len(ids) > 1)

        first, second = group[0], group[1]
        for cid in (first,):
            self.handle.transition_content(cid, ContentStatus.AI_REVIEW)
            self.handle.transition_content(cid, ContentStatus.READY_FOR_REVIEW)
            self.handle.approve_content(cid, approved_by="human:j")

        self.assertTrue(self.handle.get_content(first).is_approved)
        self.assertFalse(self.handle.get_content(second).is_approved)
        self.assertNotEqual(
            self.handle.get_content(first).current_fingerprint(),
            self.handle.get_content(second).current_fingerprint(),
        )

    def test_briefs_are_not_remapped_into_social_posts(self):
        source = Copywriter(ModelContentWriter(FakeContentProvider())).draft(
            _planned(kind=AssetKind.CAROUSEL_BRIEF), _brand()
        )
        for variant in make_variants(source, ["linkedin", "x"], _brand()):
            self.assertIs(variant.kind, AssetKind.CAROUSEL_BRIEF)

    def test_a_blocked_model_draft_still_lands_in_the_queue_visibly(self):
        rogue = FakeContentProvider(
            response=json.dumps(
                {
                    "title": "Numbers",
                    "body": "88% of teams saw gains.",
                    "cta": "Book a demo",
                }
            )
        )
        package = self._build(rogue, ["linkedin"])
        self.assertTrue(package.posts[0].blocked)
        self.assertTrue(package.posts[0].warnings)


class TestServiceWriterSelection(unittest.TestCase):
    def _service(self, tmp: str, provider: Any = None) -> tuple[Path, GrowthService]:
        root = _root(tmp)
        service = GrowthService(root, writer_provider=provider)
        service.init_workspace("acme")
        service.onboard(
            "acme",
            brand_voice="Direct.",
            objectives=["Generate 50 demos"],
            audience_personas=["VP Talent"],
            brand_assets=["assets/logo.svg"],
            prohibited_content=["Unverified stats"],
            cadence_per_week=1,
            platforms=[{"platform": "linkedin", "account_label": "Acme page"}],
            weekly_review_day="sunday",
            weekly_review_hour_utc=17,
        )
        campaign = service.create_campaign("acme", "Launch", objective="Generate 50 demos")
        service.transition_campaign("acme", campaign["id"], "active")
        return root, service

    def test_a_configured_provider_produces_model_drafts(self):
        with TemporaryDirectory() as tmp:
            provider = FakeContentProvider()
            _, service = self._service(tmp, provider)
            package = service.generate_week("acme", week_start=T0, mode="model")
            self.assertTrue(package["posts"])
            self.assertTrue(provider.prompts)

    def test_explicit_template_mode_does_not_call_the_provider(self):
        with TemporaryDirectory() as tmp:
            provider = FakeContentProvider()
            _, service = self._service(tmp, provider)
            service.generate_week("acme", week_start=T0, mode="template")
            self.assertEqual(provider.prompts, [])

    def test_model_mode_without_a_provider_is_an_error_not_a_fallback(self):
        with TemporaryDirectory() as tmp:
            _, service = self._service(tmp, None)
            with self.assertRaises(ModelGenerationError) as ctx:
                service.generate_week("acme", week_start=T0, mode="model")
            self.assertIn("no AI provider is configured", str(ctx.exception))

    def test_template_mode_works_without_a_provider(self):
        with TemporaryDirectory() as tmp:
            _, service = self._service(tmp, None)
            package = service.generate_week("acme", week_start=T0, mode="template")
            self.assertTrue(package["posts"])
            for post in package["posts"]:
                item_meta = service.get_content("acme", post["content_id"])
                self.assertEqual(item_meta["metadata"]["generation_method"], "template")

    def test_omitting_the_mode_is_an_error_not_a_default(self):
        """Neither path is chosen implicitly: the caller must state which ran."""
        with TemporaryDirectory() as tmp:
            _, service = self._service(tmp, FakeContentProvider())
            with self.assertRaises(ModelGenerationError) as ctx:
                service.generate_week("acme", week_start=T0)
            self.assertIn("must be stated explicitly", str(ctx.exception))

    def test_an_unknown_mode_is_refused(self):
        with TemporaryDirectory() as tmp:
            _, service = self._service(tmp, FakeContentProvider())
            with self.assertRaises(ModelGenerationError):
                service.generate_week("acme", week_start=T0, mode="creative")

    def test_a_configured_provider_does_not_make_model_mode_implicit(self):
        """A provider being present must not silently select model drafting."""
        with TemporaryDirectory() as tmp:
            provider = FakeContentProvider()
            _, service = self._service(tmp, provider)
            service.generate_week("acme", week_start=T0, mode="template")
            self.assertEqual(provider.prompts, [])

    def test_approving_a_week_needs_no_generation_mode(self):
        """
        Approval is not a drafting act.

        The week was already generated; approving it re-reads the stored package
        and nothing is written. Requiring a mode here would make an already-drafted
        package impossible to approve - which is exactly what it did.
        """
        with TemporaryDirectory() as tmp:
            _, service = self._service(tmp, None)
            package = service.generate_week("acme", week_start=T0, mode="template")
            result = service.approve_week("acme", package["id"], by="human:test")
            self.assertTrue(result["approved"])

    def test_approving_a_week_never_consults_the_provider(self):
        """Approval must not reach a model, even when one is configured."""
        with TemporaryDirectory() as tmp:
            provider = FakeContentProvider()
            _, service = self._service(tmp, provider)
            package = service.generate_week("acme", week_start=T0, mode="template")
            before = len(provider.prompts)
            service.approve_week("acme", package["id"], by="human:test")
            self.assertEqual(len(provider.prompts), before)


class TestEnhancedReview(unittest.TestCase):
    """Claim-risk escalation: a review layer, not a fact-checker."""

    def _asset(self, body: str):
        return Copywriter(
            ModelContentWriter(
                FakeContentProvider(
                    response=json.dumps(
                        {
                            "title": "Draft",
                            "body": body,
                            "cta": "Book a demo",
                        }
                    )
                )
            )
        ).draft(_planned(), _brand())

    def test_a_numeric_claim_requires_enhanced_review(self):
        asset = self._asset("We cut sourcing time by 40 hours a month.")
        self.assertTrue(asset.requires_enhanced_review)
        self.assertIn("numeric-claim", asset.claim_risks)

    def test_a_comparative_claim_requires_enhanced_review(self):
        asset = self._asset("Our approach is faster than starting from scratch.")
        self.assertIn("comparative-claim", asset.claim_risks)

    def test_a_customer_claim_requires_enhanced_review(self):
        asset = self._asset("Our customers keep their hiring judgement.")
        self.assertIn("customer-claim", asset.claim_risks)

    def test_a_legal_claim_requires_enhanced_review(self):
        asset = self._asset("The workspace is GDPR compliant by design.")
        self.assertIn("legal-claim", asset.claim_risks)

    def test_a_financial_claim_requires_enhanced_review(self):
        asset = self._asset("Teams see real cost reduction within a quarter.")
        self.assertIn("financial-claim", asset.claim_risks)

    def test_an_external_fact_requires_enhanced_review(self):
        asset = self._asset("Research shows recruiters re-source constantly.")
        self.assertIn("unverifiable-external-fact", asset.claim_risks)

    def test_plain_copy_needs_no_enhanced_review(self):
        asset = self._asset("We store the person once, not once per requisition.")
        self.assertFalse(asset.requires_enhanced_review)
        self.assertEqual(asset.claim_risks, [])

    def test_enhanced_review_does_not_block_the_draft(self):
        """Escalation, not blocking: the draft is normal and may reach review."""
        asset = self._asset("We cut sourcing time by 40 hours a month.")
        self.assertTrue(asset.requires_enhanced_review)
        self.assertFalse(asset.is_blocked)
        self.assertTrue(gate_for_review(asset, _brand())[0])

    def test_reclassification_runs_on_the_current_draft(self):
        from growth.generation import enhanced_review_for

        asset = self._asset("We store the person once.")
        self.assertFalse(enhanced_review_for(asset)[0])
        asset.draft += " Teams report 3x faster sourcing."
        needs, risks, _ = enhanced_review_for(asset)
        self.assertTrue(needs)
        self.assertIn("numeric-claim", risks)

    def test_an_absence_of_flags_is_not_a_verification(self):
        from growth.generation import classify_claims

        payload = classify_claims("We store the person once.").to_dict()
        self.assertFalse(payload["requires_enhanced_review"])
        self.assertIn("not a fact-checking engine", payload["note"])

    def test_enhanced_review_survives_into_the_approval_inbox(self):
        with TemporaryDirectory() as tmp:
            root = _root(tmp)
            store = GrowthStore(root)
            store.init_workspace("acme")
            handle = store.open("acme")
            campaign = handle.create_campaign(name="Launch", theme="sourcing craft")
            handle.transition_campaign(campaign.id, CampaignStatus.ACTIVE)

            provider = FakeContentProvider(
                response=json.dumps(
                    {
                        "title": "Results",
                        "body": "Teams cut 40 hours a month.",
                        "cta": "Book a demo",
                    }
                )
            )
            plan = ContentPlanner("acme").plan_week(
                week_start=T0,
                cadence=1,
                recommendations=[],
                campaigns=[{**handle.get_campaign(campaign.id).to_dict(), "accepts_content": True}],
                platforms=["linkedin"],
            )
            package = WeeklyPackageBuilder(handle, Copywriter(ModelContentWriter(provider))).build(
                brand=_brand(), plan=plan, recommendations=[], experiments=[], now=T0
            )

            self.assertTrue(package.posts[0].requires_enhanced_review)
            self.assertIn("numeric-claim", package.posts[0].claim_risks)
            self.assertTrue(any("ENHANCED review" in w for w in package.warnings))

            from growth.generation import ApprovalInbox

            inbox = ApprovalInbox(handle)
            row = inbox.items()[0]
            self.assertTrue(row.requires_enhanced_review)
            self.assertIn("numeric-claim", row.claim_risks)
            self.assertEqual(row.priority, "enhanced-review")
            self.assertEqual(inbox.summary()["enhanced_review"], 1)

            # It escalates rather than blocking: the item can still be reviewed.
            self.assertTrue(inbox.submit_for_review(row.content_id)["ok"])
            self.assertTrue(ApprovalInbox(handle).items()[0].requires_enhanced_review)


if __name__ == "__main__":
    unittest.main()
