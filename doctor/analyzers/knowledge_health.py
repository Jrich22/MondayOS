"""KnowledgeHealthAnalyzer — inspects the MondayOS knowledge base."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from doctor.base import BaseAnalyzer
from doctor.finding import Finding, Severity
from doctor.result import AnalyzerResult

_CATEGORY = "knowledge"


class KnowledgeHealthAnalyzer(BaseAnalyzer):
    """
    Audits the MondayOS knowledge base for structural issues.

    Checks:
      - Entry count (INFO)
      - Entries with empty body (WARNING)
      - Entries with broken superseded_by references (WARNING)
      - Entries with no tags (INFO)
      - Orphaned import index entries (entries in .import_index.json whose
        knowledge files have been deleted) (WARNING)
    """

    NAME = "knowledge"

    def analyze(self) -> AnalyzerResult:
        start = time.monotonic()
        findings: list[Finding] = []

        if self._monday is None:
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.INFO,
                title="Knowledge analysis skipped (no Monday instance)",
            ))
            return AnalyzerResult(name=self.NAME, findings=findings,
                                  duration_ms=(time.monotonic() - start) * 1000)

        try:
            from knowledge.store import KnowledgeStore
            store = KnowledgeStore(self._root)
            entries = store.list_all()  # ACTIVE entries only
        except Exception as exc:
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.WARNING,
                title="Could not load knowledge store",
                detail=str(exc),
                recommendation="Check knowledge/ directory for corruption.",
            ))
            return AnalyzerResult(name=self.NAME, findings=findings,
                                  duration_ms=(time.monotonic() - start) * 1000)

        total = len(entries)
        findings.append(Finding(
            category=_CATEGORY,
            severity=Severity.INFO,
            title=f"{total} active knowledge entry/entries",
            data={"entry_count": total},
        ))

        if total == 0:
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.WARNING,
                title="Knowledge base is empty",
                recommendation=(
                    "Run `monday migrate` to import existing project documentation, "
                    "or use `monday learn` to add entries manually."
                ),
            ))
            return AnalyzerResult(name=self.NAME, findings=findings,
                                  duration_ms=(time.monotonic() - start) * 1000)

        # Entries with empty body
        empty_body = [e for e in entries if not e.body.strip()]
        if empty_body:
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.WARNING,
                title=f"{len(empty_body)} entry/entries with empty body",
                detail="\n".join(f"  {e.id}: {e.title}" for e in empty_body[:10]),
                recommendation="Add content to entries with empty bodies, or archive them.",
                data={"empty_body_ids": [e.id for e in empty_body]},
            ))

        # Entries with no tags
        no_tags = [e for e in entries if not e.tags]
        if no_tags:
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.INFO,
                title=f"{len(no_tags)} entry/entries with no tags",
                detail="\n".join(f"  {e.id}: {e.title}" for e in no_tags[:10]),
                recommendation="Add tags to improve searchability.",
                data={"untagged_ids": [e.id for e in no_tags]},
            ))
        else:
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.OK,
                title="All entries have at least one tag",
            ))

        # Broken superseded_by references
        all_ids = {e.id for e in entries}
        broken_refs = [
            e for e in entries
            if e.superseded_by and e.superseded_by not in all_ids
        ]
        if broken_refs:
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.WARNING,
                title=f"{len(broken_refs)} entry/entries with broken superseded_by reference(s)",
                detail="\n".join(f"  {e.id} → {e.superseded_by} (not found)" for e in broken_refs),
                recommendation="Repair or clear dangling superseded_by fields.",
                data={"broken_refs": [{"id": e.id, "missing": e.superseded_by} for e in broken_refs]},
            ))

        # Import index orphans
        findings.extend(_check_import_index(self._root, all_ids))

        # Entry type breakdown (INFO)
        from collections import Counter
        type_counts = Counter(e.entry_type.value for e in entries)
        findings.append(Finding(
            category=_CATEGORY,
            severity=Severity.INFO,
            title="Entry type breakdown",
            detail="\n".join(f"  {k}: {v}" for k, v in sorted(type_counts.items())),
            data={"by_type": dict(type_counts)},
        ))

        return AnalyzerResult(
            name=self.NAME,
            findings=findings,
            duration_ms=(time.monotonic() - start) * 1000,
        )


def _check_import_index(root: Path, active_ids: set[str]) -> list[Finding]:
    """Check .import_index.json for entries that no longer exist on disk."""
    index_path = root / "knowledge" / ".import_index.json"
    if not index_path.exists():
        return []

    try:
        import json
        data = json.loads(index_path.read_text(encoding="utf-8"))
        refs = data.get("source_refs", {})
    except Exception:
        return []

    orphans = [
        (source_ref, meta.get("entry_id", "?"))
        for source_ref, meta in refs.items()
        if meta.get("entry_id") not in active_ids and meta.get("entry_id") != "[dry-run]"
    ]

    if orphans:
        detail = "\n".join(f"  {sr} → {eid}" for sr, eid in orphans[:10])
        return [Finding(
            category=_CATEGORY,
            severity=Severity.WARNING,
            title=f"{len(orphans)} orphaned import index entry/entries",
            detail=f"Import index references entries that no longer exist:\n{detail}",
            recommendation=(
                "Run `monday migrate rollback <run-id>` or manually clean "
                "knowledge/.import_index.json."
            ),
            data={"orphan_count": len(orphans)},
        )]

    return []
