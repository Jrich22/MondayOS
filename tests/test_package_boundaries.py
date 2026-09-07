"""
Tests for the package dependency graph.

MondayOS's layering is one of its better properties — `intelligence` is a leaf,
`initiatives` sits on it, `reasoning` on both, and `workspace` consumes reasoning
without reasoning knowing workspace exists. That discipline is easy to hold while
someone is watching and easy to lose in one convenient import, so it is asserted
here rather than trusted.

The cycle this file was written to prevent had a specific shape: `ProjectRegistry`
— a foundational concept with no MondayOS dependencies of its own — lived in the
`monday` facade because a past Architecture Freeze discouraged new packages. Five
packages needed it, so `growth` imported `monday`, while `monday` imported
`growth.service`. Neither import was unreasonable on its own.
"""

from __future__ import annotations

import ast
import unittest
from collections import defaultdict
from pathlib import Path

PACKAGES = (
    "advisor",
    "agents",
    "brain",
    "core",
    "dashboard_api",
    "doctor",
    "events",
    "growth",
    "initiatives",
    "integrations",
    "intelligence",
    "knowledge",
    "memory",
    "migrate",
    "monday",
    "orchestrator",
    "reasoning",
    "retention",
    "search",
    "tasks",
    "workflows",
    "workspace",
)

ROOT = Path(__file__).resolve().parent.parent


def _dependencies() -> dict[str, set[str]]:
    """Which top-level packages each package imports from."""
    known = set(PACKAGES)
    deps: dict[str, set[str]] = defaultdict(set)
    for package in PACKAGES:
        directory = ROOT / package
        if not directory.is_dir():
            continue
        for file in directory.rglob("*.py"):
            try:
                tree = ast.parse(file.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    top = node.module.split(".")[0]
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split(".")[0]
                        if top in known and top != package:
                            deps[package].add(top)
                    continue
                else:
                    continue
                if top in known and top != package:
                    deps[package].add(top)
    return deps


def _cycles(deps: dict[str, set[str]]) -> list[list[str]]:
    """Every import cycle, as a list of package paths."""
    found: list[list[str]] = []
    seen_signatures: set[frozenset[str]] = set()

    def walk(node: str, path: list[str], visiting: set[str]) -> None:
        for other in sorted(deps.get(node, ())):
            if other in visiting:
                cycle = path[path.index(other) :] + [other]
                signature = frozenset(cycle)
                if signature not in seen_signatures:
                    seen_signatures.add(signature)
                    found.append(cycle)
                continue
            walk(other, [*path, other], visiting | {other})

    for package in PACKAGES:
        walk(package, [package], {package})
    return found


class TestNoImportCycles(unittest.TestCase):
    def test_there_are_zero_cycles_between_packages(self):
        cycles = _cycles(_dependencies())
        self.assertEqual(
            cycles,
            [],
            "import cycle(s): " + "; ".join(" -> ".join(c) for c in cycles),
        )

    def test_the_growth_monday_cycle_specifically_stays_gone(self):
        """
        The regression this file exists for. Named explicitly so a reintroduction
        reads as "you recreated the known cycle" rather than as a generic failure.
        """
        deps = _dependencies()
        self.assertNotIn("monday", deps.get("growth", set()))


class TestLayering(unittest.TestCase):
    """Direction, not just acyclicity: a leaf that grows a dependency is a smell."""

    def test_core_depends_on_nothing(self):
        self.assertEqual(_dependencies().get("core", set()), set())

    def test_intelligence_is_a_leaf(self):
        """
        Retrieval must not know about reasoning, initiatives, or the workspace.
        Everything above it reads from it; it reads from nothing.
        """
        self.assertEqual(_dependencies().get("intelligence", set()), set())

    def test_reasoning_does_not_depend_on_the_workspace(self):
        """
        The seam that lets a conversation consume reasoning without reasoning
        modelling conversations.
        """
        self.assertNotIn("workspace", _dependencies().get("reasoning", set()))

    def test_initiatives_does_not_depend_on_reasoning(self):
        """Initiatives are a domain; reasoning consumes them, not the reverse."""
        self.assertNotIn("reasoning", _dependencies().get("initiatives", set()))

    def test_project_identity_lives_below_the_facade(self):
        """
        `ProjectRegistry` in `monday` was the cause of the only cycle. Packages
        that need it must reach `core`, never the facade.
        """
        self.assertTrue((ROOT / "core" / "project.py").is_file())
        self.assertFalse((ROOT / "monday" / "project.py").exists())
        for package in ("growth", "workspace", "initiatives"):
            with self.subTest(package=package):
                self.assertNotIn("monday", _dependencies().get(package, set()))


if __name__ == "__main__":
    unittest.main()
