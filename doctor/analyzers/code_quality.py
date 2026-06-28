"""CodeQualityAnalyzer — TODO/FIXME scan, large files, empty directories."""
from __future__ import annotations

import re
import time
from pathlib import Path

from doctor.base import BaseAnalyzer
from doctor.finding import Finding, Severity
from doctor.result import AnalyzerResult

_CATEGORY = "code-quality"

# Markers to scan for in Python source files
_MARKERS = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b", re.IGNORECASE)

# Thresholds
_LARGE_FILE_BYTES = 500 * 1024   # 500 KB
_MANY_MARKERS = 10               # warn when total marker count exceeds this

# Paths to exclude from scanning
_EXCLUDE_PARTS = {"__pycache__", ".venv", ".git", "node_modules", ".mypy_cache", ".pytest_cache"}


class CodeQualityAnalyzer(BaseAnalyzer):
    """
    Scans Python source files for quality signals.

    Checks:
      - TODO / FIXME / HACK / XXX markers
      - Large files (>500 KB)
      - Empty directories in the source tree
    """

    NAME = "code-quality"

    def analyze(self) -> AnalyzerResult:
        start = time.monotonic()
        findings: list[Finding] = []

        py_files = _collect_py_files(self._root)
        all_files = _collect_all_files(self._root)

        # TODO/FIXME scan
        marker_hits: list[str] = []
        for path in py_files:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                for i, line in enumerate(text.splitlines(), 1):
                    if _MARKERS.search(line):
                        rel = path.relative_to(self._root)
                        marker_hits.append(f"{rel}:{i}: {line.strip()[:120]}")
            except OSError:
                pass

        if not marker_hits:
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.OK,
                title="No TODO/FIXME/HACK markers found",
            ))
        elif len(marker_hits) <= _MANY_MARKERS:
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.INFO,
                title=f"{len(marker_hits)} TODO/FIXME marker(s) in source",
                detail="\n".join(marker_hits),
                recommendation="Review and resolve or track these items as tasks.",
                data={"marker_count": len(marker_hits)},
            ))
        else:
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.WARNING,
                title=f"{len(marker_hits)} TODO/FIXME markers — consider triaging",
                detail="\n".join(marker_hits[:20]),
                recommendation=(
                    f"Found {len(marker_hits)} markers. Run `monday migrate` to import "
                    "known technical debt into the knowledge base."
                ),
                data={"marker_count": len(marker_hits)},
            ))

        # Large files
        large_files: list[tuple[Path, int]] = []
        for path in all_files:
            try:
                size = path.stat().st_size
                if size > _LARGE_FILE_BYTES:
                    large_files.append((path, size))
            except OSError:
                pass

        if large_files:
            detail_lines = [
                f"{p.relative_to(self._root)}  ({s // 1024} KB)"
                for p, s in sorted(large_files, key=lambda t: t[1], reverse=True)
            ]
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.WARNING,
                title=f"{len(large_files)} large file(s) over 500 KB",
                detail="\n".join(detail_lines),
                recommendation="Consider moving large binaries or data files to external storage.",
                data={"large_files": [str(p.relative_to(self._root)) for p, _ in large_files]},
            ))
        else:
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.OK,
                title="No large files (>500 KB) found",
            ))

        # Empty directories
        empty_dirs = _find_empty_dirs(self._root)
        if empty_dirs:
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.INFO,
                title=f"{len(empty_dirs)} empty director{'ies' if len(empty_dirs) != 1 else 'y'} found",
                detail="\n".join(str(d.relative_to(self._root)) for d in empty_dirs[:15]),
                recommendation="Remove or populate empty directories to reduce project noise.",
                data={"empty_dirs": [str(d.relative_to(self._root)) for d in empty_dirs]},
            ))

        return AnalyzerResult(
            name=self.NAME,
            findings=findings,
            duration_ms=(time.monotonic() - start) * 1000,
        )


def _collect_py_files(root: Path) -> list[Path]:
    return [
        p for p in root.rglob("*.py")
        if not _excluded(p, root)
    ]


def _collect_all_files(root: Path) -> list[Path]:
    return [
        p for p in root.rglob("*")
        if p.is_file() and not _excluded(p, root)
    ]


def _find_empty_dirs(root: Path) -> list[Path]:
    empty = []
    for d in root.rglob("*"):
        if not d.is_dir() or _excluded(d, root):
            continue
        try:
            children = list(d.iterdir())
            if not children:
                empty.append(d)
        except OSError:
            pass
    return empty


def _excluded(path: Path, root: Path) -> bool:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return True
    return bool(_EXCLUDE_PARTS & set(parts))
