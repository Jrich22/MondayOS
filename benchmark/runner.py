"""
Running the benchmark.

One pass over every corpus, scored identically, compared against the committed
baseline. The provider guard runs first: a result produced with a model in the
call path is not a baseline, and finding that out afterwards is too late.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from benchmark.corpus import Corpus, discover
from benchmark.guard import assert_provider_free
from benchmark.probes import probe
from benchmark.report import CorpusReport, Report, Verdict, load_baseline
from benchmark.scoring import compare, score

# The project whose engineering vocabulary the leak markers describe. Passed to
# the probe so it can skip self-detection; scoring never sees it, which is what
# keeps the standard identical for every corpus.
MARKER_OWNER = "mondayos"


def run(
    config_dir: Path | None = None,
    cache_root: Path | None = None,
    corpora: list[Corpus] | None = None,
) -> Report:
    """
    Measure every available corpus.

    ``cache_root`` defaults to a fresh temporary directory so a run cannot
    inherit state from a previous one — determinism has to be a property of the
    measurement, not of what happened to be cached.
    """
    assert_provider_free()

    resolved = corpora if corpora is not None else discover(config_dir or Path("config"))
    report = Report()

    with tempfile.TemporaryDirectory(prefix="mondayos-bench-") as tmp:
        cache = cache_root or Path(tmp)
        for corpus in resolved:
            measured = probe(corpus, cache, own_markers=corpus.slug == MARKER_OWNER)
            if not measured.available:
                report.corpora[corpus.slug] = CorpusReport(
                    project=corpus.slug, available=False, reason=measured.reason
                )
                continue
            assertions, observations = score(measured)
            report.corpora[corpus.slug] = CorpusReport(
                project=corpus.slug,
                available=True,
                assertions=assertions.to_dict(),
                observations=observations.to_dict(),
                volatile=measured.volatile,
            )
    return report


def verdict(report: Report, baseline: dict[str, Any] | None = None) -> Verdict:
    """
    Judge a report against the committed baseline.

    Assertion violations fail regardless of the baseline: they are invariants,
    not measurements, so there is nothing to compare them to.
    """
    base = baseline if baseline is not None else load_baseline()
    result = Verdict()

    for slug in sorted(report.corpora):
        corpus = report.corpora[slug]
        if not corpus.available:
            result.skipped.append(f"{slug}: {corpus.reason}")
            continue

        from benchmark.scoring import Assertions

        violations = Assertions(**corpus.assertions).violations()
        result.failures.extend(f"{slug}: {v}" for v in violations)

        recorded = (base.get("corpora") or {}).get(slug)
        if not recorded or not recorded.get("available"):
            result.improvements.append(f"{slug}: no baseline recorded for this corpus")
            continue
        failures, improvements = compare(
            slug, corpus.observations, recorded.get("observations", {})
        )
        result.failures.extend(failures)
        result.improvements.extend(improvements)

    return result
