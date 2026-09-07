"""
Proof that the benchmark never reaches a model provider.

The harness is worth trusting only if it measures MondayOS's deterministic
machinery. One accidental import of `Monday` or `WorkspaceService` would pull a
provider into the call path, make results depend on a network round-trip, and
turn a repeatable number into a sampled one — while still looking like it worked.

So this is checked rather than intended. `assert_provider_free` inspects what the
benchmark modules actually import, transitively, and names the offender if the
rule is ever broken.
"""

from __future__ import annotations

import ast
from pathlib import Path

# Packages a benchmark run must never reach. `brain` holds the provider
# abstraction; `monday` and `workspace` construct one; the vendor SDKs are the
# thing itself.
FORBIDDEN: frozenset[str] = frozenset(
    {"brain", "monday", "workspace", "anthropic", "openai", "dashboard_api"}
)

_PACKAGE = Path(__file__).resolve().parent


_REPO = _PACKAGE.parent


def _imports_of(package: Path) -> set[str]:
    """Top-level packages imported anywhere in one package's modules."""
    found: set[str] = set()
    for file in sorted(package.rglob("*.py")):
        try:
            tree = ast.parse(file.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module.split(".")[0])
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(alias.name.split(".")[0])
    return found


def imported_packages() -> set[str]:
    """
    Every first-party package the benchmark can reach, transitively.

    Transitive rather than direct, because direct imports prove very little: the
    benchmark imports `reasoning`, and if `reasoning` ever imported `workspace` a
    provider would be two hops away and a one-level check would miss it.

    Deliberately *not* a check of `sys.modules`. That was the first version and it
    was unsound: it asks whether this process has ever imported a provider, which
    in a shared test run is true because some other test did, and has nothing to
    do with whether the benchmark reaches one. A static walk answers the question
    actually being asked.
    """
    seen: set[str] = set()
    queue = [_PACKAGE]
    while queue:
        current = queue.pop()
        for name in _imports_of(current):
            if name in seen:
                continue
            seen.add(name)
            candidate = _REPO / name
            if candidate.is_dir() and (candidate / "__init__.py").exists():
                queue.append(candidate)
    return seen


def assert_provider_free() -> None:
    """
    Raise if the benchmark could reach a model provider.

    A benchmark result that depends on a network call is not a baseline, and the
    failure would be invisible: the numbers would still look plausible.
    """
    reachable = imported_packages() & FORBIDDEN
    if reachable:
        raise AssertionError(
            f"benchmark can reach provider package(s): {sorted(reachable)}. "
            "The harness must measure MondayOS's deterministic machinery only."
        )
