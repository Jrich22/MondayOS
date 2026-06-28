"""KnowledgeCandidate — a pre-validation knowledge object extracted from a source document."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class KnowledgeCandidate:
    """
    A knowledge entry extracted from a source document, before validation and import.

    Candidates are produced by parsers and consumed by MigrationEngine. They carry
    enough metadata for idempotency checking (source_ref + fingerprint), human
    review (extraction_notes), and direct conversion to a Monday.learn() call.

    Attributes:
        title:            Entry title.
        entry_type:       MKS knowledge type string (sprint, decision, pattern, etc.).
        content:          Full Markdown body for the knowledge entry.
        summary:          One-line summary (auto-extracted if empty).
        tags:             Searchable tag list.
        components:       Component/module names this entry covers.
        source_ref:       Stable unique identifier for this candidate within its source.
                          Format: "{source_name}:{discriminator}", e.g. "changelog:0.7.0".
        source_file:      Relative path of the source file (for traceability).
        fingerprint:      SHA-256[:16] of content — used for content-change detection.
        confidence:       Extraction confidence 0.0–1.0. Below 0.5 is flagged in reports.
        extraction_notes: Human-readable notes about what was extracted and any caveats.
        metadata:         Extra key/value pairs passed through to the knowledge entry.
    """

    title: str
    entry_type: str
    content: str
    source_ref: str
    source_file: str
    summary: str = ""
    tags: list[str] = field(default_factory=list)
    components: list[str] = field(default_factory=list)
    fingerprint: str = ""
    confidence: float = 0.9
    extraction_notes: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.fingerprint:
            self.fingerprint = _fingerprint(self.content)
        if not self.summary:
            self.summary = _extract_summary(self.content)


def _fingerprint(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def _extract_summary(content: str, max_len: int = 200) -> str:
    """Extract a one-line summary from Markdown content."""
    for line in content.splitlines():
        stripped = line.strip()
        # Skip headings, horizontal rules, bold/italic openers, table rows, bullets
        if not stripped:
            continue
        if stripped.startswith(("#", "---", "**", "|", "-", "*", "+")):
            continue
        return stripped[:max_len]
    # Fallback: compress the whole content
    return content.replace("\n", " ").strip()[:max_len]


def slugify(text: str) -> str:
    """Convert a title to a stable lowercase slug for source_ref discriminators."""
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[-\s]+", "-", text)
    return text.strip("-")[:60]
