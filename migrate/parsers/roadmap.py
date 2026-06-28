"""Parser for docs/ROADMAP.md — extracts phase Documentation and milestone Feature entries."""
from __future__ import annotations

import re

from migrate.candidate import KnowledgeCandidate, slugify
from migrate.parsers.base import BaseParser

# Matches: ## Phase 1 — Single-User Local System
_PHASE_RE = re.compile(r"^## (Phase \d+ — .+)$", re.MULTILINE)

# Matches: #### 1.1 — Core Engine and Integration Layer
_MILESTONE_RE = re.compile(r"^#### ([\d.]+) — (.+)$", re.MULTILINE)

# Skip boilerplate sections
_SKIP_TITLES = {
    "how this roadmap works",
    "what is deliberately out of scope (across all phases)",
    "revision policy",
    "current status: foundation (pre-phase 1)",
    "phase 1 milestones",
    "phase 1 exit criteria (all must pass)",
}


class RoadmapParser(BaseParser):
    """
    Extracts planning artifacts from docs/ROADMAP.md.

    Phase sections (## Phase N —) → Documentation entries capturing the phase goals.
    Milestone sections (#### X.Y —) → Feature entries capturing planned deliverables.
    """

    SOURCE_NAME = "roadmap"
    SOURCE_FILE = "docs/ROADMAP.md"
    DESCRIPTION = "Phase documentation and feature milestones from docs/ROADMAP.md"
    ENTRY_TYPES = ["documentation", "feature"]

    def parse(self, text: str) -> list[KnowledgeCandidate]:
        candidates: list[KnowledgeCandidate] = []

        # Extract phase-level sections
        phase_sections = re.split(r"\n(?=## Phase \d+)", text)
        for section in phase_sections:
            section = section.strip()
            m = _PHASE_RE.match(section)
            if not m:
                continue
            title = m.group(1).strip()
            if title.lower() in _SKIP_TITLES:
                continue

            # Extract the phase intro (before first ####)
            intro_lines = []
            for line in section.splitlines():
                if line.startswith("####"):
                    break
                intro_lines.append(line)
            intro = "\n".join(intro_lines).strip()
            if not intro:
                continue

            summary = _extract_goal(intro) or title
            phase_num = re.search(r"Phase (\d+)", title)
            pnum = phase_num.group(1) if phase_num else "?"
            tags = ["roadmap", f"phase-{pnum}", "documentation"]

            candidates.append(KnowledgeCandidate(
                title=title,
                entry_type="documentation",
                content=intro,
                summary=summary,
                tags=tags,
                components=[],
                source_ref=f"roadmap:phase-{pnum}",
                source_file=self.SOURCE_FILE,
                confidence=0.90,
                extraction_notes=f"Phase-level goal extracted from ROADMAP.md",
                metadata={"phase": int(pnum) if pnum.isdigit() else pnum},
            ))

        # Extract milestone sections
        milestone_matches = list(_MILESTONE_RE.finditer(text))
        for i, match in enumerate(milestone_matches):
            milestone_id = match.group(1)  # "1.1"
            milestone_title = match.group(2).strip()

            start = match.start()
            end = milestone_matches[i + 1].start() if i + 1 < len(milestone_matches) else len(text)
            section = text[start:end].strip()
            section = re.sub(r"\n---\s*$", "", section).strip()

            summary = _extract_goal(section) or milestone_title
            tags = ["roadmap", "milestone", f"milestone-{milestone_id}", "feature"]

            candidates.append(KnowledgeCandidate(
                title=f"Milestone {milestone_id} — {milestone_title}",
                entry_type="feature",
                content=section,
                summary=summary,
                tags=tags,
                components=[],
                source_ref=f"roadmap:milestone-{milestone_id}",
                source_file=self.SOURCE_FILE,
                confidence=0.88,
                extraction_notes=f"Milestone {milestone_id} extracted from ROADMAP.md",
                metadata={"milestone": milestone_id},
            ))

        return candidates


def _extract_goal(text: str) -> str:
    """Extract **Goal:** line or first meaningful paragraph."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("**Goal:**"):
            return stripped[len("**Goal:**"):].strip()[:200]
    # Fallback: first non-header non-empty line
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and not stripped.startswith("**"):
            return stripped[:200]
    return ""
