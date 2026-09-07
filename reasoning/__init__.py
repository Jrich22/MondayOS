"""
Reasoning — the layer between retrieval and response.

MondayOS retrieves well. This package is what lets it *conclude*: it reads the
project index, relationship graph, task store and git history into structured
facts, applies named rules to reach inferences, ranks recommendations against each
other, and scores every claim from the strength of its evidence.

Nothing here calls a model. An `Assessment` is built deterministically and handed
to a responder to narrate, which is the inversion the package exists for — the
system reasons, the model explains. That makes the reasoning assertable in tests
and stable across model changes, neither of which is true of a prompt.

The public surface is `ReasoningEngine` and the value types it returns.
"""

from reasoning.confidence import for_recommendation, score
from reasoning.engine import ReasoningEngine
from reasoning.executive import Routing, Topic, route
from reasoning.facts import Area, ProjectFacts, gather
from reasoning.gaps import as_work_items
from reasoning.inference import RULES, Rule, infer
from reasoning.models import (
    Assessment,
    Band,
    Claim,
    ClaimKind,
    Confidence,
    Gap,
    Mode,
    Recommendation,
)

__all__ = [
    "RULES",
    "Area",
    "Assessment",
    "Band",
    "Claim",
    "ClaimKind",
    "Confidence",
    "Gap",
    "Mode",
    "ProjectFacts",
    "ReasoningEngine",
    "Recommendation",
    "Routing",
    "Rule",
    "Topic",
    "as_work_items",
    "for_recommendation",
    "gather",
    "infer",
    "route",
    "score",
]
