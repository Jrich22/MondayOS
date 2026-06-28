"""Parser for docs/DECISIONS.md — extracts one Decision entry per ADR."""
from __future__ import annotations

import re

from migrate.candidate import KnowledgeCandidate
from migrate.parsers.base import BaseParser

# Matches: ## ADR-001: Python as the Primary Implementation Language
_ADR_RE = re.compile(r"^## (ADR-(\d+): (.+))$", re.MULTILINE)

# Extract the ### Decision section body
_DECISION_BODY_RE = re.compile(
    r"### Decision\s*\n(.*?)(?=\n###|\n---|\Z)",
    re.DOTALL,
)


class DecisionsParser(BaseParser):
    """
    Extracts Decision knowledge entries from docs/DECISIONS.md.

    Each ADR section (## ADR-NNN: Title) becomes one Decision entry.
    The decision body is used as the summary when available.
    """

    SOURCE_NAME = "decisions"
    SOURCE_FILE = "docs/DECISIONS.md"
    DESCRIPTION = "Architectural Decision Records from docs/DECISIONS.md"
    ENTRY_TYPES = ["decision"]

    def parse(self, text: str) -> list[KnowledgeCandidate]:
        candidates: list[KnowledgeCandidate] = []
        seen_adrs: set[str] = set()

        # Split on ADR headers; keep each section together
        sections = re.split(r"\n(?=## ADR-\d+:)", text)

        for section in sections:
            section = section.strip()
            if not section:
                continue

            m = _ADR_RE.match(section)
            if not m:
                continue

            full_title = m.group(1).strip()  # "ADR-001: Python as..."
            adr_num = m.group(2).zfill(3)    # "001"
            short_title = m.group(3).strip()  # "Python as..."

            if adr_num in seen_adrs:
                continue
            seen_adrs.add(adr_num)

            # Strip trailing horizontal rule
            body = re.sub(r"\n---\s*$", "", section).strip()

            # Try to extract the decision statement as summary
            summary = _extract_decision_summary(body) or short_title

            # Extract date if present
            date_match = re.search(r"\*\*Date:\*\*\s*(\S+)", body)
            date = date_match.group(1) if date_match else ""

            tags = ["adr", "architectural-decision", f"adr-{adr_num}"]

            candidates.append(KnowledgeCandidate(
                title=full_title,
                entry_type="decision",
                content=body,
                summary=summary,
                tags=tags,
                components=[],
                source_ref=f"decisions:ADR-{adr_num}",
                source_file=self.SOURCE_FILE,
                confidence=0.97,
                extraction_notes=f"Extracted from DECISIONS.md ({full_title})",
                metadata={"adr_number": int(adr_num), "date": date},
            ))

        return candidates


def _extract_decision_summary(section: str) -> str:
    """Extract the text of the ### Decision subsection as summary."""
    m = _DECISION_BODY_RE.search(section)
    if not m:
        return ""
    lines = [ln.strip() for ln in m.group(1).splitlines() if ln.strip()]
    if lines:
        return lines[0][:200]
    return ""
