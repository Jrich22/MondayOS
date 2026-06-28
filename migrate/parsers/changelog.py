"""Parser for docs/CHANGELOG.md — extracts one Sprint entry per version block."""
from __future__ import annotations

import re

from migrate.candidate import KnowledgeCandidate
from migrate.parsers.base import BaseParser

# Matches: ## [0.7.0] — 2026-06-28 — Sprint 1.6: End-to-End Workflow
_HEADER_RE = re.compile(
    r"^## \[(\d+\.\d+\.\d+)\] — (\d{4}-\d{2}-\d{2}) — (.+)$",
    re.MULTILINE,
)


class ChangelogParser(BaseParser):
    """
    Extracts Sprint knowledge entries from CHANGELOG.md.

    Each versioned section (## [x.y.z] — date — title) becomes one Sprint entry.
    The ## [Unreleased] section is skipped. Duplicate versions (same [x.y.z])
    produce only the first occurrence.
    """

    SOURCE_NAME = "changelog"
    SOURCE_FILE = "docs/CHANGELOG.md"
    DESCRIPTION = "Sprint entries from each version block in CHANGELOG.md"
    ENTRY_TYPES = ["sprint"]

    def parse(self, text: str) -> list[KnowledgeCandidate]:
        candidates: list[KnowledgeCandidate] = []
        seen_versions: set[str] = set()

        # Split on version headers; keep delimiter with its section
        sections = re.split(r"\n(?=## \[)", text)

        for section in sections:
            section = section.strip()
            if not section:
                continue

            m = _HEADER_RE.match(section)
            if not m:
                continue  # ## [Unreleased] or non-version headers

            version = m.group(1)
            date = m.group(2)
            title = m.group(3).strip()

            if version in seen_versions:
                continue
            seen_versions.add(version)

            # Strip trailing horizontal rule
            body = re.sub(r"\n---\s*$", "", section).strip()

            # Extract a summary from Added/Changed subsections
            summary = _extract_sprint_summary(body) or title

            tags = ["sprint", f"v{version}"]
            # Derive sprint number from title if present
            sprint_match = re.search(r"Sprint\s+([\d.]+)", title)
            if sprint_match:
                tags.append(f"sprint-{sprint_match.group(1)}")

            candidates.append(KnowledgeCandidate(
                title=title,
                entry_type="sprint",
                content=body,
                summary=summary,
                tags=tags,
                components=[],
                source_ref=f"changelog:{version}",
                source_file=self.SOURCE_FILE,
                confidence=0.95,
                extraction_notes=f"Extracted from CHANGELOG.md version {version} ({date})",
                metadata={"version": version, "date": date},
            ))

        return candidates


def _extract_sprint_summary(section: str) -> str:
    """Pull the first bullet from ### Added as a summary."""
    in_added = False
    for line in section.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("### added"):
            in_added = True
            continue
        if in_added and stripped.startswith("-"):
            # First bullet: strip leading "- " and backticks
            raw = stripped.lstrip("- ").strip()
            raw = re.sub(r"`([^`]+)`", r"\1", raw)
            return raw[:200]
        if in_added and stripped.startswith("###"):
            break
    return ""
