"""
The MondayOS benchmark — a repeatable, provider-free measurement of itself.

Its purpose is to make a change to retrieval or discovery arguable. Before this,
"initiative discovery got better" was a claim; now it is a diff against a
committed baseline, measured the same way across four projects.

What it measures is deliberately narrow: dimensions with a right answer that
needs no judgement. Routing register, groundedness, citation targets, discovered
initiatives, cross-project leakage. What it does not measure — and must not be
read as measuring — is whether MondayOS's advice is any good. A green run means
the machinery behaves; it says nothing about whether a recommendation was worth
taking.

Run it with `python -m benchmark`, or `--record` to re-record the baseline after
a change that was reviewed and intended.
"""

from benchmark.cases import ALL_CASES, Case, Dimension
from benchmark.corpus import Corpus, discover
from benchmark.guard import assert_provider_free
from benchmark.report import Report, Verdict, load_baseline, save_baseline
from benchmark.runner import run

__all__ = [
    "ALL_CASES",
    "Case",
    "Corpus",
    "Dimension",
    "Report",
    "Verdict",
    "assert_provider_free",
    "discover",
    "load_baseline",
    "run",
    "save_baseline",
]
