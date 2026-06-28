"""MigrationEngine — orchestrates the source → candidate → validate → import pipeline."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from migrate.candidate import KnowledgeCandidate
from migrate.errors import ParseError, RollbackError, SourceNotFoundError, UnknownSourceError
from migrate.parsers.base import BaseParser, SourceInfo
from migrate.parsers.changelog import ChangelogParser
from migrate.parsers.decisions import DecisionsParser
from migrate.parsers.roadmap import RoadmapParser
from migrate.parsers.self_hosting import SelfHostingParser
from migrate.parsers.session_log import SessionLogParser
from migrate.parsers.workflows import WorkflowsParser
from migrate.report import (
    FailedEntry,
    ImportReport,
    ImportedEntry,
    RollbackReport,
    SkippedEntry,
)

# Canonical registry of all supported sources.
_PARSERS: list[BaseParser] = [
    ChangelogParser(),
    DecisionsParser(),
    SessionLogParser(),
    RoadmapParser(),
    WorkflowsParser(),
    SelfHostingParser(),
]

_PARSER_MAP: dict[str, BaseParser] = {p.SOURCE_NAME: p for p in _PARSERS}

# File that tracks imported source_refs → entry_ids for idempotency.
_IMPORT_INDEX_FILENAME = ".import_index.json"

# Minimum confidence to import without flagging
_MIN_CONFIDENCE = 0.5


class MigrationEngine:
    """
    Orchestrates the full import pipeline: parse → validate → deduplicate → import.

    All source document files are resolved relative to project_root. The import
    index (knowledge/.import_index.json) persists source_ref→entry_id mappings
    across runs, making all imports idempotent.

    Args:
        monday:       An initialised Monday instance. All learn() calls go through it.
        project_root: Root of the MondayOS project (docs/, knowledge/, logs/ live here).
        logs_dir:     Directory where migration run reports are written.
    """

    def __init__(
        self,
        monday: Any,
        project_root: Path,
        logs_dir: Path,
    ) -> None:
        self._monday = monday
        self._project_root = project_root
        self._logs_dir = logs_dir
        self._index_path = project_root / "knowledge" / _IMPORT_INDEX_FILENAME

    # ------------------------------------------------------------------
    # Source inspection
    # ------------------------------------------------------------------

    def list_sources(self) -> list[SourceInfo]:
        """Return metadata for all registered sources."""
        return [p.source_info() for p in _PARSERS]

    def source_exists(self, source_name: str) -> bool:
        """True if the source is registered and its file exists on disk."""
        parser = _PARSER_MAP.get(source_name)
        if not parser:
            return False
        return (self._project_root / parser.SOURCE_FILE).exists()

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(
        self,
        sources: list[str] | None = None,
        dry_run: bool = False,
        overwrite: bool = False,
        progress_callback: Any = None,
    ) -> ImportReport:
        """
        Execute the migration pipeline.

        Args:
            sources:           Source names to process. None means all registered sources.
            dry_run:           If True, parse and validate but do not write any entries.
            overwrite:         If True, re-import entries that already exist in the index.
            progress_callback: Optional callable(message: str) for progress reporting.

        Returns:
            ImportReport describing what was imported, skipped, and failed.

        Raises:
            UnknownSourceError: if a requested source name is not registered.
        """
        requested = sources or list(_PARSER_MAP.keys())

        # Validate source names up front
        for name in requested:
            if name not in _PARSER_MAP:
                raise UnknownSourceError(name)

        report = ImportReport.start(sources=requested, dry_run=dry_run)
        index = self._load_index()

        def _emit(msg: str) -> None:
            if progress_callback:
                progress_callback(msg)

        for source_name in requested:
            parser = _PARSER_MAP[source_name]
            source_path = self._project_root / parser.SOURCE_FILE

            if not source_path.exists():
                _emit(f"[{source_name}] source file not found: {parser.SOURCE_FILE} — skipping")
                continue

            _emit(f"[{source_name}] parsing {parser.SOURCE_FILE}")
            try:
                text = source_path.read_text(encoding="utf-8")
                candidates = parser.parse(text)
            except Exception as exc:
                _emit(f"[{source_name}] parse error: {exc}")
                report.failed.append(FailedEntry(
                    source_ref=f"{source_name}:__parse__",
                    title=source_name,
                    error=str(exc),
                ))
                continue

            report.candidates_found += len(candidates)
            _emit(f"[{source_name}] found {len(candidates)} candidate(s)")

            for i, candidate in enumerate(candidates, 1):
                _emit(f"  [{i}/{len(candidates)}] {candidate.title[:60]}")
                result = self._process_candidate(
                    candidate, report, index, dry_run, overwrite
                )
                _emit(f"    → {result}")

        # Write the import index (only on real runs, not dry runs)
        if not dry_run:
            self._save_index(index)

        # Write the run report
        log_path = report.write(self._logs_dir)
        _emit(f"Report written: {log_path}")

        return report

    # ------------------------------------------------------------------
    # Rollback
    # ------------------------------------------------------------------

    def rollback(self, run_id: str) -> RollbackReport:
        """
        Remove all entries created in a prior run identified by run_id.

        Looks for run_id[:8] as prefix in logs_dir. Removes each entry
        by setting it to ARCHIVED status (soft delete) and removes it from
        the import index.

        Raises:
            RollbackError: if the run report cannot be found.
        """
        report_path = self._find_report(run_id)
        if not report_path:
            raise RollbackError(run_id, "Run report not found in logs directory")

        import_report = ImportReport.load(report_path)
        rollback = RollbackReport(run_id=run_id)

        # Load the import index
        index = self._load_index()

        for entry in import_report.imported:
            try:
                # Remove from the knowledge store via the Monday internal path
                self._monday._remove_knowledge_entry(entry.entry_id)
                rollback.removed.append(entry.entry_id)
                # Remove from import index
                index["source_refs"].pop(entry.source_ref, None)
            except Exception as exc:
                rollback.failed.append(entry.entry_id)
                rollback.message = f"Partial rollback: {exc}"

        self._save_index(index)
        rollback.message = rollback.message or (
            f"Removed {len(rollback.removed)} entries"
            + (f"; {len(rollback.failed)} failed" if rollback.failed else "")
        )
        return rollback

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _process_candidate(
        self,
        candidate: KnowledgeCandidate,
        report: ImportReport,
        index: dict[str, Any],
        dry_run: bool,
        overwrite: bool,
    ) -> str:
        """Process one candidate: validate, deduplicate, import. Returns a status string."""
        source_ref = candidate.source_ref

        # Validation
        if not candidate.content.strip():
            report.skipped.append(SkippedEntry(
                source_ref=source_ref,
                title=candidate.title,
                reason="empty_content",
            ))
            return "skipped: empty content"

        if candidate.confidence < _MIN_CONFIDENCE:
            report.skipped.append(SkippedEntry(
                source_ref=source_ref,
                title=candidate.title,
                reason="low_confidence",
            ))
            return f"skipped: confidence {candidate.confidence:.0%} below threshold"

        # Duplicate detection
        existing = index["source_refs"].get(source_ref)
        if existing and not overwrite:
            report.skipped.append(SkippedEntry(
                source_ref=source_ref,
                title=candidate.title,
                reason="duplicate",
            ))
            return f"skipped: already imported as {existing['entry_id']}"

        # Dry run
        if dry_run:
            report.imported.append(ImportedEntry(
                source_ref=source_ref,
                entry_id="[dry-run]",
                title=candidate.title,
                entry_type=candidate.entry_type,
            ))
            return f"would import ({candidate.entry_type})"

        # Import
        try:
            r = self._monday.learn(
                content=candidate.content,
                title=candidate.title,
                entry_type=candidate.entry_type,
                tags=candidate.tags,
                components=candidate.components,
            )
            if not r.accepted:
                report.failed.append(FailedEntry(
                    source_ref=source_ref,
                    title=candidate.title,
                    error=r.message,
                ))
                return f"failed: {r.message}"

            entry_id = r.entry_id
            report.imported.append(ImportedEntry(
                source_ref=source_ref,
                entry_id=entry_id,
                title=candidate.title,
                entry_type=candidate.entry_type,
            ))
            index["source_refs"][source_ref] = {
                "entry_id": entry_id,
                "fingerprint": candidate.fingerprint,
                "run_id": report.run_id,
                "title": candidate.title,
            }
            return f"imported → {entry_id}"

        except Exception as exc:
            report.failed.append(FailedEntry(
                source_ref=source_ref,
                title=candidate.title,
                error=str(exc),
            ))
            return f"failed: {exc}"

    def _load_index(self) -> dict[str, Any]:
        """Load the import index from disk, or return an empty index."""
        if self._index_path.exists():
            try:
                data = json.loads(self._index_path.read_text(encoding="utf-8"))
                if "source_refs" in data:
                    return data
            except (json.JSONDecodeError, OSError):
                pass
        return {"source_refs": {}}

    def _save_index(self, index: dict[str, Any]) -> None:
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        self._index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")

    def _find_report(self, run_id: str) -> Path | None:
        """Find a report file by run_id prefix."""
        if not self._logs_dir.exists():
            return None
        short = run_id[:8]
        # Exact match first
        exact = self._logs_dir / f"{short}.json"
        if exact.exists():
            return exact
        # Prefix scan
        for path in self._logs_dir.glob("*.json"):
            data = json.loads(path.read_text())
            if data.get("run_id", "").startswith(short) or data.get("run_id") == run_id:
                return path
        return None
