"""Parser for docs/SELF_HOSTING_PLAN.md — extracts opportunities, workflows, and phase docs."""
from __future__ import annotations

import re

from migrate.candidate import KnowledgeCandidate, slugify
from migrate.parsers.base import BaseParser

_OPPORTUNITY_RE = re.compile(
    r"^### (Opportunity \d+ — .+)$", re.MULTILINE
)
_WORKFLOW_DESIGN_RE = re.compile(
    r"^### (Workflow [A-Z] — `.+?`)$", re.MULTILINE
)
_PART_RE = re.compile(r"^## (Part \d+ — .+)$", re.MULTILINE)


class SelfHostingParser(BaseParser):
    """
    Extracts knowledge entries from docs/SELF_HOSTING_PLAN.md.

    Opportunity sections → Feature entries (improvement opportunities).
    Workflow design sections → Runbook entries (workflow specifications).
    Part-level summaries → Documentation entries (strategic context).
    """

    SOURCE_NAME = "self-hosting"
    SOURCE_FILE = "docs/SELF_HOSTING_PLAN.md"
    DESCRIPTION = "Opportunities, workflow designs, and migration phases from SELF_HOSTING_PLAN.md"
    ENTRY_TYPES = ["feature", "runbook", "documentation"]

    def parse(self, text: str) -> list[KnowledgeCandidate]:
        candidates: list[KnowledgeCandidate] = []

        candidates.extend(_extract_opportunities(text, self.SOURCE_FILE))
        candidates.extend(_extract_workflow_designs(text, self.SOURCE_FILE))
        candidates.extend(_extract_part_summaries(text, self.SOURCE_FILE))

        return candidates


def _extract_opportunities(text: str, source_file: str) -> list[KnowledgeCandidate]:
    candidates = []
    matches = list(_OPPORTUNITY_RE.finditer(text))
    for i, match in enumerate(matches):
        title = match.group(1).strip()
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        section = text[start:end].strip()

        # Extract opportunity number
        num_match = re.search(r"Opportunity (\d+)", title)
        opp_num = num_match.group(1).zfill(2) if num_match else "00"

        summary = _first_meaningful_line(section) or title
        # Extract impact/effort metadata
        impact = _extract_field(section, r"\*\*Impact:?\s*([^|]+)")
        effort = _extract_field(section, r"Effort:?\s*([^|]+)")

        candidates.append(KnowledgeCandidate(
            title=title,
            entry_type="feature",
            content=section,
            summary=summary,
            tags=["self-hosting", "opportunity", f"opportunity-{opp_num}"],
            components=["monday"],
            source_ref=f"self-hosting:opportunity-{opp_num}",
            source_file=source_file,
            confidence=0.87,
            extraction_notes=f"Opportunity {opp_num} from SELF_HOSTING_PLAN.md",
            metadata={"impact": impact, "effort": effort},
        ))
    return candidates


def _extract_workflow_designs(text: str, source_file: str) -> list[KnowledgeCandidate]:
    candidates = []
    matches = list(_WORKFLOW_DESIGN_RE.finditer(text))
    for i, match in enumerate(matches):
        title = match.group(1).strip()
        start = match.start()
        # Find next workflow design or next ## section
        if i + 1 < len(matches):
            end = matches[i + 1].start()
        else:
            next_h2 = re.search(r"^## ", text[start:], re.MULTILINE)
            end = start + next_h2.start() if next_h2 else len(text)
        section = text[start:end].strip()

        # Extract workflow name from backtick notation
        wf_name_match = re.search(r"`([^`]+)`", title)
        wf_name = wf_name_match.group(1) if wf_name_match else slugify(title)
        letter_match = re.search(r"Workflow ([A-Z])", title)
        letter = letter_match.group(1) if letter_match else "?"

        summary = f"Workflow design for '{wf_name}': multi-step process"
        trigger_match = re.search(r"\*\*Trigger:\*\*\s*(.+)", section)
        if trigger_match:
            summary = f"Triggered by: {trigger_match.group(1).strip()[:150]}"

        candidates.append(KnowledgeCandidate(
            title=f"Workflow Design: {wf_name}",
            entry_type="runbook",
            content=section,
            summary=summary,
            tags=["self-hosting", "workflow-design", wf_name, "runbook"],
            components=["workflows"],
            source_ref=f"self-hosting:workflow-{wf_name}",
            source_file=source_file,
            confidence=0.88,
            extraction_notes=f"Workflow {letter} design from SELF_HOSTING_PLAN.md",
            metadata={"workflow_name": wf_name},
        ))
    return candidates


def _extract_part_summaries(text: str, source_file: str) -> list[KnowledgeCandidate]:
    """Extract Part introductions as Documentation entries."""
    candidates = []
    matches = list(_PART_RE.finditer(text))
    for i, match in enumerate(matches):
        title = match.group(1).strip()
        start = match.start()
        # Stop before next ### (first subsection)
        section_text = text[start:]
        first_sub = re.search(r"^###", section_text, re.MULTILINE)
        intro_end = first_sub.start() if first_sub else len(section_text)
        intro = section_text[:intro_end].strip()

        if len(intro) < 50:
            continue

        part_match = re.search(r"Part (\d+)", title)
        part_num = part_match.group(1).zfill(2) if part_match else "00"
        summary = _first_meaningful_line(intro) or title

        candidates.append(KnowledgeCandidate(
            title=f"Self-Hosting: {title}",
            entry_type="documentation",
            content=intro,
            summary=summary,
            tags=["self-hosting", "documentation", f"part-{part_num}"],
            components=["monday"],
            source_ref=f"self-hosting:part-{part_num}",
            source_file=source_file,
            confidence=0.80,
            extraction_notes=f"{title} intro from SELF_HOSTING_PLAN.md",
            metadata={"part": int(part_num) if part_num.isdigit() else part_num},
        ))
    return candidates


def _first_meaningful_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and not stripped.startswith("**") \
                and not stripped.startswith("|") and not stripped.startswith("-"):
            return stripped[:200]
    return ""


def _extract_field(text: str, pattern: str) -> str:
    m = re.search(pattern, text, re.IGNORECASE)
    return m.group(1).strip().rstrip("|").strip() if m else ""
