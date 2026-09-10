"""
Running the acceptance benchmark against every available corpus.

Sequential, deliberately. The journey's later turns depend on the earlier ones
having happened, so there is nothing to parallelise even if it were polite to,
and an earlier live run taught us that hammering a provider measures the
provider's queue rather than the product.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from acceptance.gates import evaluate
from acceptance.pacing import Pacing
from acceptance.report import AcceptanceReport, stamp
from acceptance.session import ProjectSession, isolated_root
from benchmark.corpus import discover as discover_corpora


def provider_profile(provider: Any) -> dict[str, Any]:
    """What the configured provider can tell us, so gates can say what they cannot check."""
    if provider is None:
        return {"name": "", "model": "", "configured": False, "reports_stop_reason": False}
    return {
        "name": getattr(provider, "name", ""),
        "model": getattr(provider, "_model", "") or "",
        "configured": True,
        "reports_stop_reason": bool(getattr(provider, "reports_stop_reason", False)),
        "supports_streaming": bool(getattr(provider, "supports_streaming", False)),
        "is_local": bool(getattr(provider, "is_local", False)),
    }


def run(
    config_dir: Path,
    build_monday: Callable[[Path], Any],
    provider: Any,
    only: tuple[str, ...] = (),
    pacing: Pacing | None = None,
    cache_root: Path | None = None,
) -> AcceptanceReport:
    """
    Drive every available corpus through the journey.

    ``build_monday`` is handed the temporary root and returns a live Monday. It
    is injected so the harness can be tested against a scripted provider without
    a network, and so nothing here needs to know how Monday is configured.
    """
    from initiatives.discover import discover as discover_initiatives
    from intelligence.graph import build as build_graph
    from intelligence.graph import project_boundaries
    from intelligence.index import build as build_index

    pacing = pacing or Pacing()
    report = AcceptanceReport(provider=provider_profile(provider), started_at=stamp())

    with TemporaryDirectory() as tmp:
        workspace_root = isolated_root(config_dir / "projects.json", Path(tmp) / "root")
        monday = build_monday(workspace_root)
        cache = cache_root or (Path(tmp) / "cache")

        for corpus in discover_corpora(config_dir):
            if only and corpus.slug not in only:
                continue
            if not corpus.available or corpus.root is None:
                report.projects[corpus.slug] = {
                    "available": False,
                    "reason": corpus.reason,
                    "completed": False,
                }
                continue

            index = build_index(corpus.slug, corpus.root, cache_root=cache / corpus.slug)
            graph = build_graph(index, tasks=[], knowledge=[])
            initiatives = discover_initiatives(index, graph, [])
            from intelligence.questions import QuestionEngine

            engine = QuestionEngine(index, graph, tasks=[])

            session = ProjectSession(
                monday=monday,
                project=corpus.slug,
                corpus_root=corpus.root,
                monday_root=workspace_root,
                boundaries=project_boundaries(index),
                discovered=[i.name for i in initiatives] + [i.slug for i in initiatives],
                own_decisions=[_adr_of(n.label) for n in engine._own_decisions()],
                own_commits=_own_commits(corpus.root),
                pacing=pacing,
            )
            outcome = session.run(dict(corpus.nouns))
            outcome["reason"] = ""
            report.projects[corpus.slug] = outcome
            report.turns.extend(session.turns)
            report.incidents.extend(session.incidents)
            # A pause between projects as well: the next one opens with an
            # executive turn, which is the most expensive call in the journey.
            time.sleep(pacing.between_turns)

    report.gates = evaluate(
        report.turns,
        report.projects,
        {"reports_stop_reason": report.provider.get("reports_stop_reason", False)},
    )
    return report


def _own_commits(root: Path) -> frozenset[str]:
    """
    This project's own commit SHAs, scoped the way S1 scoped every other
    history read.

    `RepoScope` confines the log to the project's own paths, so a nested project
    does not inherit its parent's history and a parent does not claim its
    children's. Without this the gate would have nothing truthful to compare a
    cited commit against.
    """
    from core.vcs import git, scope_for

    scope = scope_for(root)
    try:
        output = git(scope, "log", "--format=%H", "-n", "2000")
    except Exception:  # noqa: BLE001 — a project without history simply has none
        return frozenset()
    return frozenset(line.strip().lower() for line in output.splitlines() if line.strip())


def _adr_of(label: str) -> str:
    """`ADR-017: Title [Accepted]` -> `ADR-017`."""
    return label.split(":", 1)[0].strip().upper()
