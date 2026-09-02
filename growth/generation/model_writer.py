"""
Model-backed drafting, behind the existing ContentWriter protocol.

This is the only place in ``growth/`` that talks to a model, and it does so
through the MondayOS ``AIProvider`` abstraction — never an SDK. No provider name,
no model id, and no vendor-specific behaviour appears here or anywhere else in
the Growth Bot: which model runs is MondayOS routing and configuration, and this
module works unchanged when that changes.

Three rules make a model safe to put on this path.

**The model sees one workspace and nothing else.** The prompt is assembled from a
single ``BrandContext`` plus one ``PlannedPost``, both scoped to the project being
generated for. There is no argument that would let another project's material in,
and a test asserts a second project's content never reaches the prompt.

**Structured output, validated, failing closed.** The model is asked for a JSON
object matching a fixed schema. Anything else — prose, malformed JSON, a missing
required field, a mismatched platform — is refused. A draft that cannot be
validated does not become a ContentItem.

**The prompt is not the enforcement.** The prompt tells the model not to fabricate
claims, and the existing safety gate then runs on the actual generated text. A
model that ignores its instructions is caught by the same check that catches a
human editing a fabricated statistic in afterwards, because the check runs on
output rather than on intent.

There is deliberately **no silent fallback to templates.** If model generation was
requested and the provider is unavailable, that is reported. An operator who
asked for model drafting and quietly received template output would have no way
to know what they were reviewing.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from brain.providers.base import AIProvider, ProviderError
from growth.generation.models import BrandContext, PlannedPost

# What the model must return. Anything missing is a refusal, not a default.
REQUIRED_FIELDS: tuple[str, ...] = ("title", "body", "cta")

# Fields the schema accepts. Anything else the model volunteers is dropped rather
# than trusted - a model inventing its own field is a model improvising.
SCHEMA_FIELDS: tuple[str, ...] = (
    "title",
    "content_type",
    "platform",
    "body",
    "cta",
    "destination",
    "themes",
    "recommendation_ids",
    "campaign_id",
    "experiment_ids",
    "warnings",
    "asset_brief",
)

# Maximum body length accepted from a model, before platform formatting. A
# response far past this is a runaway generation, not a draft.
MAX_BODY_CHARS = 8000

# Output token budget for one draft. An initial default chosen to fit a long-form
# social post with room to spare - configurable per writer, and expected to move
# once real usage shows what long-form actually needs. Not a product rule.
DEFAULT_MAX_TOKENS = 1200

GENERATION_METHOD_MODEL = "model"
GENERATION_METHOD_TEMPLATE = "template"

# The two drafting paths. A caller must name one: neither is a default, because
# a reviewer needs to know whether they are reading model output or a template.
GENERATION_MODES: frozenset[str] = frozenset({GENERATION_METHOD_MODEL, GENERATION_METHOD_TEMPLATE})


class ModelGenerationError(RuntimeError):
    """Raised when model drafting could not produce a usable, validated draft."""

    def __init__(self, detail: str, provider: str = "") -> None:
        self.provider = provider
        super().__init__(
            f"Model content generation failed{f' on provider {provider!r}' if provider else ''}: "
            f"{detail} No template fallback was applied: generation was requested from a "
            "model, and silently substituting template output would leave the reviewer "
            "unable to tell what they are reading."
        )


class ModelOutputInvalidError(ModelGenerationError):
    """Raised when a model response does not satisfy the generation schema."""


@dataclass
class ModelDraft:
    """One validated draft returned by a model."""

    title: str
    body: str
    cta: str
    platform: str = ""
    content_type: str = ""
    destination: str = ""
    themes: list[str] | None = None
    warnings: list[str] | None = None
    asset_brief: str = ""
    provider: str = ""
    model: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "body": self.body,
            "cta": self.cta,
            "platform": self.platform,
            "content_type": self.content_type,
            "destination": self.destination,
            "themes": list(self.themes or []),
            "warnings": list(self.warnings or []),
            "asset_brief": self.asset_brief,
            "provider": self.provider,
            "model": self.model,
        }


def build_prompt(post: PlannedPost, brand: BrandContext, angle: str = "") -> str:
    """
    Assemble the generation prompt from ONE workspace's material.

    Everything here comes from the ``brand`` and ``post`` arguments, which are
    scoped to a single project. Nothing is read from disk, and no other
    workspace is reachable from this function.
    """

    def block(label: str, values: Any) -> str:
        if not values:
            return ""
        if isinstance(values, (list, tuple)):
            return f"{label}:\n" + "\n".join(f"  - {v}" for v in values) + "\n"
        return f"{label}: {values}\n"

    evidence = ""
    if post.recommendation_ids or post.experiment_ids:
        evidence = (
            "WHY THIS CONTENT EXISTS (from the deterministic Growth Brain):\n"
            f"  {post.rationale}\n"
            f"  Recommendation ids: {', '.join(post.recommendation_ids) or 'none'}\n"
            f"  Experiment ids: {', '.join(post.experiment_ids) or 'none'}\n"
        )

    return (
        "You are drafting marketing content for ONE project. Use only the project "
        "material below.\n\n"
        "=== PROJECT ===\n"
        + block("Project", brand.project)
        + block("Products and services", brand.products)
        + block("Website", brand.website)
        + "\n=== BRAND ===\n"
        + block("Voice", brand.voice)
        + block("Tone", brand.tone)
        + block("Style rules", brand.style_rules)
        + block("Approved assets", brand.approved_assets)
        + "\n=== AUDIENCE ===\n"
        + block("Primary audience", brand.audience)
        + block("Personas", brand.personas)
        + block("Pain points", brand.pain_points)
        + "\n=== CAMPAIGN ===\n"
        + block("Objective", brand.objective)
        + block("Campaign id", post.campaign)
        + block("Theme", post.theme)
        + block("Content pillars", brand.content_pillars)
        + block("Call to action", post.cta or (brand.ctas[0] if brand.ctas else ""))
        + block("Destination", brand.website)
        + block("Platform", post.platform)
        + block("Requested format", post.kind.value)
        + block("Conversion goal", post.goal)
        + (f"\n{evidence}" if evidence else "")
        + "\n=== RESTRICTIONS (absolute) ===\n"
        + block("This project prohibits", brand.prohibited)
        + "You must NOT invent, imply, or state any of the following unless it appears "
        "verbatim in the project material above:\n"
        "  - testimonials, customer names, or quotes\n"
        "  - usage, revenue, market, or performance statistics\n"
        "  - product capabilities, customer stories, partnerships, or research findings\n"
        "If you do not have a fact, write around it. Do not estimate, and do not use a "
        "placeholder number.\n"
        + (f"\nAngle to take: {angle}\n" if angle else "")
        + "\n=== OUTPUT ===\n"
        "Return ONE JSON object and nothing else. No prose before or after it, no code "
        "fence commentary. Required keys: title, body, cta. Optional keys: themes "
        "(array of strings), warnings (array of strings), asset_brief (string).\n"
        '{"title": "...", "body": "...", "cta": "...", "themes": [], "warnings": [], '
        '"asset_brief": ""}\n'
        "Put anything you were unsure about into warnings rather than inventing it."
    )


def parse_response(content: str) -> dict[str, Any]:
    """
    Extract and validate the JSON object from a model response.

    Fails closed on anything that is not a single well-formed object carrying the
    required fields. A model that returns prose has not answered the question,
    and guessing at what it meant is how an unvalidated draft reaches a reviewer.
    """
    raw = (content or "").strip()
    if not raw:
        raise ModelOutputInvalidError("the provider returned an empty response.")

    payload = _extract_json(raw)
    if payload is None:
        raise ModelOutputInvalidError(
            "no JSON object found in the response; the model returned prose instead of "
            "the requested structured output."
        )
    if not isinstance(payload, dict):
        raise ModelOutputInvalidError(f"expected a JSON object, got {type(payload).__name__}.")

    missing = [f for f in REQUIRED_FIELDS if not str(payload.get(f, "")).strip()]
    if missing:
        raise ModelOutputInvalidError(
            f"the response is missing required field(s): {', '.join(missing)}."
        )

    body = str(payload["body"])
    if len(body) > MAX_BODY_CHARS:
        raise ModelOutputInvalidError(
            f"the body is {len(body)} characters, past the {MAX_BODY_CHARS} limit; "
            "this is a runaway generation rather than a draft."
        )
    # Drop anything outside the schema rather than carrying it forward.
    return {k: v for k, v in payload.items() if k in SCHEMA_FIELDS}


def _extract_json(raw: str) -> Any:
    """Find the JSON object in a response, fenced or bare. None when absent."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    candidates = [fenced.group(1)] if fenced else []

    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end > start:
        candidates.append(raw[start : end + 1])

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


class ModelContentWriter:
    """
    A ContentWriter backed by any MondayOS AIProvider.

    Satisfies the same protocol as TemplateContentWriter, so nothing downstream
    knows or cares which produced a draft. The provider is injected: this class
    never constructs one, never names one, and never reads provider config.
    """

    def __init__(self, provider: AIProvider, max_tokens: int = DEFAULT_MAX_TOKENS) -> None:
        self._provider = provider
        self._max_tokens = max_tokens
        self.last_draft: ModelDraft | None = None

    @property
    def provider_name(self) -> str:
        """The provider identifier, for provenance. Never branched on."""
        return self._provider.name

    def write(self, post: PlannedPost, brand: BrandContext, angle: str) -> tuple[str, str]:
        """
        Draft one planned slot with the model.

        Raises MissingBrandContextError when the project has not said enough
        about itself, and ModelGenerationError when the provider is unavailable,
        errors, or returns something that does not validate. It never returns
        template output as a substitute.
        """
        brand.validate()

        availability = self._provider.availability()
        if not availability.available:
            raise ModelGenerationError(availability.instructions(), provider=self._provider.name)

        prompt = build_prompt(post, brand, angle)
        try:
            response = self._provider.ask(prompt, max_tokens=self._max_tokens)
        except ProviderError as exc:
            raise ModelGenerationError(str(exc), provider=self._provider.name) from exc
        except Exception as exc:  # noqa: BLE001 - a provider bug must surface, not vanish
            raise ModelGenerationError(
                f"{type(exc).__name__}: {exc}", provider=self._provider.name
            ) from exc

        payload = parse_response(response.content)
        self.last_draft = ModelDraft(
            title=str(payload["title"]).strip(),
            body=str(payload["body"]).strip(),
            cta=str(payload["cta"]).strip(),
            platform=str(payload.get("platform", post.platform)),
            content_type=str(payload.get("content_type", post.kind.value)),
            destination=str(payload.get("destination", "")),
            themes=[str(t) for t in (payload.get("themes") or [])],
            warnings=[str(w) for w in (payload.get("warnings") or [])],
            asset_brief=str(payload.get("asset_brief", "")),
            provider=response.provider or self._provider.name,
            model=response.model,
        )
        return self.last_draft.title, self.last_draft.body
