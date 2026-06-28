"""ImportReport and related types — the output of a migration run."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class ImportedEntry:
    source_ref: str
    entry_id: str
    title: str
    entry_type: str


@dataclass
class SkippedEntry:
    source_ref: str
    title: str
    reason: str  # "duplicate" | "validation_failed" | "low_confidence" | "empty_content"


@dataclass
class FailedEntry:
    source_ref: str
    title: str
    error: str


@dataclass
class ImportReport:
    """
    Complete record of a single migration run.

    Written to logs/migrations/{run_id[:8]}.json after every run,
    including dry runs and partially failed runs. The run_id is the
    key for rollback operations.
    """

    run_id: str
    started_at: str
    dry_run: bool
    sources: list[str]
    candidates_found: int = 0
    imported: list[ImportedEntry] = field(default_factory=list)
    skipped: list[SkippedEntry] = field(default_factory=list)
    failed: list[FailedEntry] = field(default_factory=list)
    completed_at: str = ""
    log_path: str = ""

    @classmethod
    def start(cls, sources: list[str], dry_run: bool) -> ImportReport:
        return cls(
            run_id=str(uuid.uuid4()),
            started_at=_now(),
            dry_run=dry_run,
            sources=sources,
        )

    def finish(self, log_path: str) -> None:
        self.completed_at = _now()
        self.log_path = log_path

    @property
    def imported_count(self) -> int:
        return len(self.imported)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)

    @property
    def failed_count(self) -> int:
        return len(self.failed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "dry_run": self.dry_run,
            "sources": self.sources,
            "candidates_found": self.candidates_found,
            "imported": [
                {
                    "source_ref": e.source_ref,
                    "entry_id": e.entry_id,
                    "title": e.title,
                    "entry_type": e.entry_type,
                }
                for e in self.imported
            ],
            "skipped": [
                {"source_ref": e.source_ref, "title": e.title, "reason": e.reason}
                for e in self.skipped
            ],
            "failed": [
                {"source_ref": e.source_ref, "title": e.title, "error": e.error}
                for e in self.failed
            ],
            "log_path": self.log_path,
        }

    def write(self, logs_dir: Path) -> Path:
        logs_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{self.run_id[:8]}.json"
        path = logs_dir / filename
        path.write_text(json.dumps(self.to_dict(), indent=2))
        self.log_path = str(path)
        return path

    @classmethod
    def load(cls, path: Path) -> ImportReport:
        data = json.loads(path.read_text())
        report = cls(
            run_id=data["run_id"],
            started_at=data["started_at"],
            dry_run=data["dry_run"],
            sources=data["sources"],
            candidates_found=data.get("candidates_found", 0),
            completed_at=data.get("completed_at", ""),
            log_path=data.get("log_path", ""),
        )
        for e in data.get("imported", []):
            report.imported.append(ImportedEntry(**e))
        for e in data.get("skipped", []):
            report.skipped.append(SkippedEntry(**e))
        for e in data.get("failed", []):
            report.failed.append(FailedEntry(**e))
        return report


@dataclass
class RollbackReport:
    run_id: str
    removed: list[str] = field(default_factory=list)   # entry IDs removed
    failed: list[str] = field(default_factory=list)    # entry IDs that couldn't be removed
    message: str = ""


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()
