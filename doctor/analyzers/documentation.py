"""DocumentationAnalyzer — checks README, CHANGELOG, ADRs, and internal links."""
from __future__ import annotations

import re
import time
from pathlib import Path

from doctor.base import BaseAnalyzer
from doctor.finding import Finding, Severity
from doctor.result import AnalyzerResult

_CATEGORY = "documentation"

# Markdown link pattern: [text](target) — capture the target
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)#?]+?)(?:[#?][^)]*)?\)")

# Required top-level documents
_REQUIRED_DOCS: list[tuple[str, Severity, str]] = [
    ("README.md",          Severity.WARNING, "A README.md describes the project for new contributors."),
    ("docs/CHANGELOG.md",  Severity.WARNING, "docs/CHANGELOG.md tracks the history of changes."),
    ("docs/DECISIONS.md",  Severity.INFO,    "docs/DECISIONS.md records architectural decisions (ADRs)."),
]

# Packages to check for module-level docstrings
_PACKAGE_DIRS = [
    "brain", "core", "events", "knowledge", "memory",
    "migrate", "monday", "search", "tasks", "workflows", "doctor",
]

# Exclude these directories from link checking
_EXCLUDE_PARTS = {"__pycache__", ".venv", ".git", "node_modules"}


class DocumentationAnalyzer(BaseAnalyzer):
    """
    Checks documentation completeness and integrity.

    Checks:
      - Required documents (README, CHANGELOG, DECISIONS)
      - Module docstrings in top-level packages
      - Broken internal links in Markdown files
      - Missing module documentation files
    """

    NAME = "documentation"

    def analyze(self) -> AnalyzerResult:
        start = time.monotonic()
        findings: list[Finding] = []

        # Required documents
        for rel_path, sev, rec in _REQUIRED_DOCS:
            full = self._root / rel_path
            if not full.exists():
                findings.append(Finding(
                    category=_CATEGORY,
                    severity=sev,
                    title=f"Missing: {rel_path}",
                    recommendation=rec,
                    data={"missing_file": rel_path},
                ))
            else:
                findings.append(Finding(
                    category=_CATEGORY,
                    severity=Severity.OK,
                    title=f"Present: {rel_path}",
                ))

        # Module docstrings in top-level packages
        missing_docstring: list[str] = []
        for pkg_name in _PACKAGE_DIRS:
            init = self._root / pkg_name / "__init__.py"
            if not init.exists():
                continue
            text = init.read_text(encoding="utf-8", errors="replace")
            if not _has_module_docstring(text):
                missing_docstring.append(f"{pkg_name}/__init__.py")

        if missing_docstring:
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.INFO,
                title=f"{len(missing_docstring)} package(s) missing module docstring",
                detail="\n".join(f"  {p}" for p in missing_docstring),
                recommendation="Add a module-level docstring to each package __init__.py.",
                data={"missing_docstrings": missing_docstring},
            ))
        else:
            found_pkgs = [p for p in _PACKAGE_DIRS if (self._root / p / "__init__.py").exists()]
            if found_pkgs:
                findings.append(Finding(
                    category=_CATEGORY,
                    severity=Severity.OK,
                    title="All packages have module docstrings",
                ))

        # Broken internal Markdown links
        broken_links = _find_broken_links(self._root)
        if broken_links:
            detail = "\n".join(
                f"  {src}: [{anchor}]({target})" for src, anchor, target in broken_links[:20]
            )
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.WARNING,
                title=f"{len(broken_links)} broken internal link(s) in Markdown files",
                detail=detail,
                recommendation="Fix or remove links pointing to non-existent files.",
                data={"broken_links": [
                    {"file": src, "text": anchor, "target": target}
                    for src, anchor, target in broken_links[:20]
                ]},
            ))
        else:
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.OK,
                title="No broken internal links found in Markdown files",
            ))

        return AnalyzerResult(
            name=self.NAME,
            findings=findings,
            duration_ms=(time.monotonic() - start) * 1000,
        )


def _has_module_docstring(source: str) -> bool:
    """True if the source starts with a string literal (module docstring)."""
    stripped = source.lstrip()
    return stripped.startswith('"""') or stripped.startswith("'''") or stripped.startswith('"') or stripped.startswith("'")


def _find_broken_links(root: Path) -> list[tuple[str, str, str]]:
    """
    Scan all .md files for internal links pointing to non-existent files.
    Returns list of (relative_source_path, link_text, target_path).
    """
    broken: list[tuple[str, str, str]] = []

    md_files = [
        p for p in root.rglob("*.md")
        if not any(part in _EXCLUDE_PARTS for part in p.parts)
    ]

    for md_path in md_files:
        try:
            text = md_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        for m in _MD_LINK_RE.finditer(text):
            link_text = m.group(1)
            target = m.group(2)

            # Skip URLs (http/https/ftp), mailto, anchors
            if re.match(r"^(https?://|ftp://|mailto:)", target):
                continue
            if target.startswith("#"):
                continue

            # Resolve relative to the markdown file's directory
            resolved = (md_path.parent / target).resolve()
            try:
                resolved.relative_to(root.resolve())  # must be inside project
            except ValueError:
                continue  # external absolute path — skip

            if not resolved.exists():
                rel_src = str(md_path.relative_to(root))
                broken.append((rel_src, link_text, target))

    return broken
