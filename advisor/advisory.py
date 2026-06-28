"""Advisory — the structured output of the AdvisorEngine."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Risk:
    """
    A single engineering risk surfaced by the advisor.

    Risks are ranked by severity: critical → high → medium → low.
    The impact field explains the *consequence* of not addressing this risk.
    """

    title: str
    severity: str       # "critical" | "high" | "medium" | "low"
    category: str       # matches DoctorReport category or "knowledge" | "tasks"
    impact: str         # what happens if this is ignored
    recommendation: str
    source: str = ""    # which subsystem surfaced this

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "severity": self.severity,
            "category": self.category,
            "impact": self.impact,
            "recommendation": self.recommendation,
            "source": self.source,
        }


@dataclass
class Action:
    """
    A single recommended engineering action, ranked by expected value.

    priority=1 is the highest-value action. effort is a human label
    intended to help the reader decide what to do right now.
    """

    title: str
    priority: int       # 1 = highest value, ascending
    category: str       # "quality" | "knowledge" | "docs" | "git" | "tasks" | "config"
    rationale: str      # WHY this is recommended
    effort: str         # "minutes" | "hours" | "days"
    command: str = ""   # runnable CLI hint, if applicable

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "priority": self.priority,
            "category": self.category,
            "rationale": self.rationale,
            "effort": self.effort,
            "command": self.command,
        }


@dataclass
class Advisory:
    """
    Complete engineering advisory produced by AdvisorEngine.

    All fields have defaults so the dataclass can be partially populated
    and still serialised. The to_dict() output is what --json returns.
    """

    generated_at: str = ""
    confidence: float = 0.0  # 0.0–1.0; never 1.0 without an LLM

    # Narrative summary
    repository_summary: str = ""

    # Health (from DoctorReport)
    health_score: int = 0
    health_grade: str = ""

    # Risks (ranked by severity)
    risks: list[Risk] = field(default_factory=list)

    # Recommended actions (ranked by value)
    next_actions: list[Action] = field(default_factory=list)

    # Sprint recommendation
    sprint_goal: str = ""
    sprint_rationale: str = ""

    # Technical debt
    technical_debt_summary: str = ""
    debt_items: list[str] = field(default_factory=list)

    # Gaps
    knowledge_gaps: list[str] = field(default_factory=list)
    documentation_gaps: list[str] = field(default_factory=list)

    # Metadata
    data_sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "confidence": round(self.confidence, 2),
            "health_score": self.health_score,
            "health_grade": self.health_grade,
            "repository_summary": self.repository_summary,
            "risks": [r.to_dict() for r in self.risks],
            "next_actions": [a.to_dict() for a in self.next_actions],
            "sprint_goal": self.sprint_goal,
            "sprint_rationale": self.sprint_rationale,
            "technical_debt_summary": self.technical_debt_summary,
            "debt_items": self.debt_items,
            "knowledge_gaps": self.knowledge_gaps,
            "documentation_gaps": self.documentation_gaps,
            "data_sources": self.data_sources,
        }
