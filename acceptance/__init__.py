"""
The live acceptance benchmark: MondayOS answering real questions, end to end.

`benchmark/` measures the deterministic machinery and is forbidden from importing
a provider, which is what makes it fast, reproducible and safe to gate on. That
guarantee is also its limit: it can prove routing is correct and evidence is
scoped, and it can say nothing about whether the product actually answers when a
model is in the loop.

This package is the other half. It drives the real path -- project resolution,
routing, retrieval, isolation, discovery, reasoning, generation, persistence,
continuation, staleness -- through `WorkspaceService.send_message`, the same
entry point the dashboard uses, and measures what comes back.

Two rules shape everything here.

**Mechanical only.** Every gate is a property a program can check: a citation
resolves or it does not, a recommendation key is stable or it is not. There is no
numeric quality score, because a number invented to summarise whether an answer
was *good* would be the least trustworthy figure in the report and the one people
would quote.

**A provider's limits are not the product's failures.** An overloaded API is not
a correctness bug, and a provider that cannot report why generation stopped makes
one gate unverifiable rather than failed. Both are recorded and neither fails the
run, so the report is complete whatever the provider does.
"""

from acceptance.journeys import JOURNEY, Turn
from acceptance.report import AcceptanceReport

__all__ = ["JOURNEY", "AcceptanceReport", "Turn"]
