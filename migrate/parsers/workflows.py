"""Parser for docs/WORKFLOWS.md — extracts step-type Patterns and section Documentation."""
from __future__ import annotations

import re

from migrate.candidate import KnowledgeCandidate, slugify
from migrate.parsers.base import BaseParser

# Step-type subsections under ## Step Types: ### `ask`, ### `search`, etc.
_STEP_TYPE_RE = re.compile(r"^### `(\w+)`\s*$", re.MULTILINE)

# Top-level sections to extract as Documentation
_DOC_SECTIONS = {
    "overview",
    "workflow definition format",
    "template substitution",
    "execution lifecycle",
    "execution log",
    "adding a new workflow",
    "adding a new step type",
    "phase 2 integration points",
}


class WorkflowsParser(BaseParser):
    """
    Extracts knowledge entries from docs/WORKFLOWS.md.

    Step type subsections (### `ask`, ### `search`, etc.) → Pattern entries.
    Key documentation sections → Documentation entries.
    """

    SOURCE_NAME = "workflows"
    SOURCE_FILE = "docs/WORKFLOWS.md"
    DESCRIPTION = "Step-type patterns and workflow documentation from docs/WORKFLOWS.md"
    ENTRY_TYPES = ["pattern", "documentation", "runbook"]

    def parse(self, text: str) -> list[KnowledgeCandidate]:
        candidates: list[KnowledgeCandidate] = []

        # Extract step type patterns
        step_candidates = _extract_step_types(text, self.SOURCE_FILE)
        candidates.extend(step_candidates)

        # Extract top-level documentation sections
        doc_candidates = _extract_doc_sections(text, self.SOURCE_FILE)
        candidates.extend(doc_candidates)

        # Extract CLI + Python usage as a Runbook
        runbook = _extract_usage_runbook(text, self.SOURCE_FILE)
        if runbook:
            candidates.append(runbook)

        return candidates


def _extract_step_types(text: str, source_file: str) -> list[KnowledgeCandidate]:
    """Extract each ### `step_name` block under ## Step Types as a Pattern."""
    # Find the Step Types section
    step_types_match = re.search(r"^## Step Types\s*$", text, re.MULTILINE)
    if not step_types_match:
        return []

    # Find the next top-level section after Step Types
    next_h2 = re.search(r"^## ", text[step_types_match.end():], re.MULTILINE)
    if next_h2:
        step_section = text[step_types_match.start():step_types_match.end() + next_h2.start()]
    else:
        step_section = text[step_types_match.start():]

    candidates = []
    step_matches = list(_STEP_TYPE_RE.finditer(step_section))
    for i, match in enumerate(step_matches):
        step_name = match.group(1)
        start = match.start()
        end = step_matches[i + 1].start() if i + 1 < len(step_matches) else len(step_section)
        section = step_section[start:end].strip()

        outputs_match = re.search(r"\*\*Outputs:\*\*\s*(.+)", section)
        summary = f"Workflow step type '{step_name}'"
        if outputs_match:
            summary = f"Step type '{step_name}': outputs {outputs_match.group(1).strip()}"

        candidates.append(KnowledgeCandidate(
            title=f"Workflow Step Type: {step_name}",
            entry_type="pattern",
            content=section,
            summary=summary,
            tags=["workflow", "step-type", step_name, "pattern"],
            components=["workflows"],
            source_ref=f"workflows:step-type-{step_name}",
            source_file=source_file,
            confidence=0.92,
            extraction_notes=f"Step type '{step_name}' extracted from WORKFLOWS.md",
            metadata={"step_type": step_name},
        ))
    return candidates


def _extract_doc_sections(text: str, source_file: str) -> list[KnowledgeCandidate]:
    """Extract selected top-level sections as Documentation entries."""
    candidates = []
    sections = re.split(r"\n(?=## )", text)
    for section in sections:
        section = section.strip()
        h2_match = re.match(r"^## (.+)$", section, re.MULTILINE)
        if not h2_match:
            continue
        title = h2_match.group(1).strip()
        if title.lower() not in _DOC_SECTIONS:
            continue

        body = section
        summary = _first_meaningful_line(body) or title
        slug = slugify(title)

        candidates.append(KnowledgeCandidate(
            title=f"Workflows: {title}",
            entry_type="documentation",
            content=body,
            summary=summary,
            tags=["workflow", "documentation", slug],
            components=["workflows"],
            source_ref=f"workflows:doc-{slug}",
            source_file=source_file,
            confidence=0.85,
            extraction_notes=f"Section '{title}' extracted from WORKFLOWS.md",
        ))
    return candidates


def _extract_usage_runbook(text: str, source_file: str) -> KnowledgeCandidate | None:
    """Combine CLI Usage and Python API Usage into one Runbook."""
    cli_match = re.search(r"^## CLI Usage\s*$", text, re.MULTILINE)
    if not cli_match:
        return None

    # Collect from CLI Usage through Python API Usage
    api_match = re.search(r"^## Adding a New Workflow", text[cli_match.start():], re.MULTILINE)
    end = cli_match.start() + api_match.start() if api_match else len(text)
    content = text[cli_match.start():end].strip()

    return KnowledgeCandidate(
        title="Workflow CLI and API Usage",
        entry_type="runbook",
        content=content,
        summary="How to invoke workflows via the monday CLI and Python API",
        tags=["workflow", "runbook", "cli", "api"],
        components=["workflows", "monday"],
        source_ref="workflows:usage-runbook",
        source_file=source_file,
        confidence=0.90,
        extraction_notes="CLI + Python API usage sections from WORKFLOWS.md",
    )


def _first_meaningful_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and not stripped.startswith("**"):
            return stripped[:200]
    return ""
