"""
Finding what is missing, and phrasing it as work.

The behaviour this module replaces is the one that made Monday feel like a search
engine: reporting an absence and stopping. "There is no testing strategy" is
true, useless, and ends the conversation. "Write a testing strategy covering
`workspace` and `intelligence`, which together hold 31 source files and 4 test
files" is the same observation pointed at an action.

So a `Gap` always carries a `suggested_task`. That is not a formatting preference —
it is the difference between reporting and advising, and it is enforced by the type
rather than left to whoever writes the next rule.

**Absence is weaker evidence than presence**, and this module is careful about it.
Concluding a thing does not exist because the index did not find it is a claim
about the index as much as about the project: a roadmap might live in a wiki, a
tracker, or someone's head. Gaps are therefore scoped to what MondayOS can
legitimately see — files in the repository — and `reasoning.confidence` prices
gap-driven advice below evidence-driven advice for exactly this reason.
"""

from __future__ import annotations

import re
from typing import Any

from intelligence.evidence import Citation, CitationKind, Evidence
from intelligence.index import ProjectIndex
from intelligence.models import FileKind
from reasoning.facts import ProjectFacts
from reasoning.models import Gap

# Documents a project of any maturity is expected to have, with the filename
# patterns that would satisfy each. Patterns are generous: this looks for a
# document that does the job, not for one with a particular name.
_EXPECTED_DOCS: tuple[tuple[str, re.Pattern[str], str, str, int], ...] = (
    (
        "roadmap",
        re.compile(r"roadmap|plan\.md|milestones", re.I),
        "no roadmap document",
        "There is no written statement of what comes next, so sequencing lives "
        "in conversation rather than anywhere a collaborator could read it.",
        1,
    ),
    (
        "testing strategy",
        re.compile(r"testing|test[-_]?strategy|quality", re.I),
        "no testing strategy",
        "Without a stated strategy, test coverage grows by habit and nobody can "
        "say which areas are deliberately untested versus accidentally so.",
        2,
    ),
    (
        "architecture",
        re.compile(r"architect|design\.md|system[-_]?design|overview", re.I),
        "no architecture overview",
        "A reader has no entry point above the file level, so onboarding means "
        "reading source in dependency order.",
        2,
    ),
    (
        "launch checklist",
        re.compile(r"launch|release|checklist|runbook|deploy", re.I),
        "no launch or release checklist",
        "Readiness becomes a judgement call made under time pressure rather than "
        "a list agreed in advance.",
        3,
    ),
)


def _doc_paths(index: ProjectIndex) -> list[str]:
    return [
        path
        for path, entry in index.files.items()
        if entry.kind in (FileKind.DOCUMENTATION, FileKind.DECISION)
    ]


def find(facts: ProjectFacts, index: ProjectIndex) -> list[Gap]:
    """
    Gaps in the project, most severe first.

    Ordering matters more than completeness here. A gap list nobody triages is a
    backlog, and a backlog is what people already have; the point of ranking is
    that the first two entries are worth acting on this week.
    """
    gaps: list[Gap] = []
    docs = _doc_paths(index)

    gaps.extend(_missing_documents(docs))
    gaps.extend(_missing_tests(facts))
    gaps.extend(_missing_decisions(facts))

    gaps.sort(key=lambda g: (g.severity, g.subject))
    return gaps


def _missing_documents(docs: list[str]) -> list[Gap]:
    out: list[Gap] = []
    for subject, pattern, missing, why, severity in _EXPECTED_DOCS:
        if any(pattern.search(path) for path in docs):
            continue
        evidence = Evidence()
        # Cite what *was* searched. A gap whose evidence is empty is
        # indistinguishable from a gap nobody looked for.
        for path in docs[:5]:
            evidence.add(
                Citation(
                    kind=CitationKind.FILE,
                    reference=path,
                    path=path,
                    because="documentation searched for this subject",
                )
            )
        out.append(
            Gap(
                subject=subject,
                missing=missing,
                why_it_matters=why,
                suggested_task=f"Write a {subject} document for this project",
                severity=severity,
                evidence=evidence,
            )
        )
    return out


def _missing_tests(facts: ProjectFacts) -> list[Gap]:
    out: list[Gap] = []
    for area in facts.substantial_areas[:3]:
        if area.test_files:
            continue
        evidence = Evidence()
        evidence.add(
            Citation(
                kind=CitationKind.FILE,
                reference=area.name,
                path=area.name,
                because=f"{area.source_files} source files, no matching test file",
            )
        )
        out.append(
            Gap(
                subject=area.name,
                missing=f"no tests for {area.name}",
                why_it_matters=(
                    f"{area.source_files} source files and {area.symbols} definitions "
                    "can change without anything noticing."
                ),
                suggested_task=f"Add a test suite covering {area.name}",
                severity=1,
                evidence=evidence,
            )
        )
    return out


def _missing_decisions(facts: ProjectFacts) -> list[Gap]:
    out: list[Gap] = []
    for area in facts.substantial_areas[:3]:
        if area.decisions:
            continue
        evidence = Evidence()
        evidence.add(
            Citation(
                kind=CitationKind.FILE,
                reference=area.name,
                path=area.name,
                because="area references no ADR",
            )
        )
        out.append(
            Gap(
                subject=area.name,
                missing=f"no ADR covering {area.name}",
                why_it_matters=(
                    "The rationale for how this area is built is not written down, "
                    "so it will be re-litigated or silently violated."
                ),
                suggested_task=f"Record an ADR for the {area.name} design",
                severity=2,
                evidence=evidence,
            )
        )
    return out


def as_work_items(gaps: list[Gap]) -> list[dict[str, Any]]:
    """
    Gaps rendered as proposed task payloads.

    Deliberately returns plain dictionaries and creates nothing. Automatically
    writing tasks into the TaskManager is a side effect on the user's real backlog,
    and a reasoning layer that silently files work is one people stop asking
    questions of. Increment 5 adds the approved write path; this is the read half,
    and it is the half that is safe to run on every question.
    """
    return [
        {
            "title": gap.suggested_task,
            "objective": gap.why_it_matters,
            "context": f"{gap.subject}: {gap.missing}",
            "priority": f"P{gap.severity}",
            "source": "reasoning.gaps",
        }
        for gap in gaps
    ]
