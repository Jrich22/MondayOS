"""Parser for logs/SESSION_LOG.md — extracts Sprint and Lesson entries."""
from __future__ import annotations

import re

from migrate.candidate import KnowledgeCandidate, slugify
from migrate.parsers.base import BaseParser

# Matches: ## 2026-06-27 — Sprint 1.2: Knowledge Capture
_SESSION_RE = re.compile(r"^## (\d{4}-\d{2}-\d{2}) — (.+)$", re.MULTILINE)

# Sections to skip — they duplicate CHANGELOG
_SKIP_PATTERNS = [
    re.compile(r"git checkpoint", re.IGNORECASE),
]


class SessionLogParser(BaseParser):
    """
    Extracts Sprint entries from logs/SESSION_LOG.md.

    Each top-level session section (## YYYY-MM-DD — Title) becomes one Sprint entry,
    unless it is a Git Checkpoint section (which duplicates CHANGELOG content).

    "Key decisions" bullets within sessions are not separately extracted here —
    they are captured via the DecisionsParser from DECISIONS.md.
    """

    SOURCE_NAME = "session-log"
    SOURCE_FILE = "logs/SESSION_LOG.md"
    DESCRIPTION = "Sprint session summaries from logs/SESSION_LOG.md"
    ENTRY_TYPES = ["sprint", "lesson"]

    def parse(self, text: str) -> list[KnowledgeCandidate]:
        candidates: list[KnowledgeCandidate] = []

        # Split on top-level session headers
        sections = re.split(r"\n(?=## \d{4}-\d{2}-\d{2} — )", text)

        for section in sections:
            section = section.strip()
            if not section:
                continue

            m = _SESSION_RE.match(section)
            if not m:
                continue

            date = m.group(1)
            title = m.group(2).strip()

            # Skip git checkpoint sections — they duplicate CHANGELOG
            if any(p.search(title) for p in _SKIP_PATTERNS):
                continue

            body = re.sub(r"\n---\s*$", "", section).strip()
            summary = _extract_session_summary(body) or title
            tags = _derive_tags(title, date)
            source_ref = f"session-log:{date}-{slugify(title)}"

            candidates.append(KnowledgeCandidate(
                title=f"Session: {title}",
                entry_type="sprint",
                content=body,
                summary=summary,
                tags=tags,
                components=[],
                source_ref=source_ref,
                source_file=self.SOURCE_FILE,
                confidence=0.80,
                extraction_notes=f"Session log entry for {date}: {title}",
                metadata={"session_date": date, "session_title": title},
            ))

            # Extract "Known technical debt" bullets as Bug candidates
            for bug_candidate in _extract_bugs(body, date, source_ref):
                candidates.append(bug_candidate)

        return candidates


def _extract_session_summary(section: str) -> str:
    """Extract text from ### Session Summary subsection."""
    in_summary = False
    lines = []
    for line in section.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("### session summary"):
            in_summary = True
            continue
        if in_summary:
            if stripped.startswith("###") or stripped.startswith("---"):
                break
            if stripped:
                lines.append(stripped)
                if len(lines) >= 2:
                    break
    return " ".join(lines)[:200] if lines else ""


def _derive_tags(title: str, date: str) -> list[str]:
    tags = ["sprint", "session-log"]
    sprint_match = re.search(r"Sprint\s+([\d.]+)", title)
    if sprint_match:
        tags.append(f"sprint-{sprint_match.group(1)}")
    return tags


def _extract_bugs(section: str, date: str, parent_ref: str) -> list[KnowledgeCandidate]:
    """Extract known technical debt items as Bug candidates."""
    bugs = []
    in_debt = False
    for line in section.splitlines():
        stripped = line.strip()
        if "known technical debt" in stripped.lower() or "technical debt" in stripped.lower():
            in_debt = True
            continue
        if in_debt and stripped.startswith("###"):
            break
        if in_debt and stripped.startswith("-"):
            content = stripped.lstrip("- ").strip()
            content = re.sub(r"`([^`]+)`", r"\1", content)
            if len(content) > 10:
                slug = slugify(content[:40])
                bugs.append(KnowledgeCandidate(
                    title=f"Tech Debt: {content[:80]}",
                    entry_type="bug",
                    content=f"## Known Technical Debt\n\nSource: {date} session log\n\n{content}",
                    summary=content[:200],
                    tags=["technical-debt", "session-log"],
                    components=[],
                    source_ref=f"session-log:debt-{date}-{slug}",
                    source_file="logs/SESSION_LOG.md",
                    confidence=0.65,
                    extraction_notes=f"Extracted from 'Known technical debt' section in {date} session",
                ))
    return bugs
