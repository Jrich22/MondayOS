"""
Tests for reading a repository's shape.

Every index here is fabricated rather than read from a real project. That is
deliberate: this module exists because discovery was calibrated on MondayOS, and
testing it against MondayOS would repeat the mistake with extra steps. The
layouts below are the ones that broke the old rules -- everything under a
container, a flat tree of small packages, a directory holding other projects --
described in the abstract so a passing test means the *shape* is handled.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from initiatives import layout
from intelligence.index import ProjectIndex
from intelligence.models import FileKind, IndexedFile


def index_of(paths: dict[str, FileKind], project: str = "fixture") -> ProjectIndex:
    """A ProjectIndex with nothing in it but the paths and kinds under test."""
    return ProjectIndex(
        project=project,
        root=Path("/nonexistent"),
        files={
            path: IndexedFile(path=path, kind=kind, size=0, mtime=0, lines=0, digest="")
            for path, kind in paths.items()
        },
    )


def source(*paths: str) -> dict[str, FileKind]:
    return {path: FileKind.SOURCE for path in paths}


class TestSingular(unittest.TestCase):
    def test_regular_plurals_collapse(self):
        self.assertEqual(layout.singular("guests"), "guest")
        self.assertEqual(layout.singular("policies"), "policy")
        self.assertEqual(layout.singular("boxes"), "box")

    def test_irregular_plurals_collapse(self):
        self.assertEqual(layout.singular("people"), "person")

    def test_short_and_double_s_words_are_left_alone(self):
        # `css` and `class` are not plurals; stripping the s would mangle them.
        self.assertEqual(layout.singular("css"), "css")
        self.assertEqual(layout.singular("access"), "access")

    def test_structural_names_match_in_either_number(self):
        """Singularising the input alone would let `components` through as `component`."""
        self.assertTrue(layout.is_structural("components"))
        self.assertTrue(layout.is_structural("component"))
        self.assertFalse(layout.is_structural("checkin"))


class TestContainerDetection(unittest.TestCase):
    def test_a_directory_holding_everything_is_a_container(self):
        shape = layout.analyse(
            index_of(
                source(
                    *[f"src/checkin/a{i}.ts" for i in range(5)],
                    *[f"src/guests/b{i}.ts" for i in range(5)],
                )
            )
        )
        self.assertEqual(shape.containers, frozenset({"src"}))

    def test_the_rule_is_shape_not_name(self):
        """
        `src` is not special. A project using `source/`, `app/` or `backend/`
        must get the same answer, because the alternative is a list of blessed
        names that is wrong for the next project that does something else.
        """
        for name in ("source", "app", "backend", "packages"):
            shape = layout.analyse(
                index_of(
                    source(
                        *[f"{name}/billing/a{i}.py" for i in range(5)],
                        *[f"{name}/scheduling/b{i}.py" for i in range(5)],
                    )
                )
            )
            self.assertEqual(shape.containers, frozenset({name}), name)

    def test_a_flat_project_has_no_container(self):
        shape = layout.analyse(
            index_of(
                source(
                    *[f"safety/a{i}.py" for i in range(8)],
                    *[f"research/b{i}.py" for i in range(6)],
                    *[f"ops/c{i}.py" for i in range(6)],
                )
            )
        )
        self.assertTrue(shape.is_flat)

    def test_a_directory_with_one_child_is_not_a_container(self):
        """A container is a *layout*; one child is just a package."""
        shape = layout.analyse(index_of(source(*[f"src/only/a{i}.ts" for i in range(9)])))
        self.assertEqual(shape.containers, frozenset())

    def test_a_split_project_reads_as_flat(self):
        # Neither half holds enough to be the container, so both are capabilities.
        shape = layout.analyse(
            index_of(
                source(
                    *[f"web/pages/a{i}.ts" for i in range(6)],
                    *[f"web/lib/b{i}.ts" for i in range(6)],
                    *[f"server/routes/c{i}.py" for i in range(6)],
                    *[f"server/models/d{i}.py" for i in range(6)],
                )
            )
        )
        self.assertTrue(shape.is_flat)


class TestProjectBoundaries(unittest.TestCase):
    def test_a_directory_holding_projects_is_a_boundary(self):
        files = source(*[f"growth/a{i}.py" for i in range(5)])
        files["projects/cue-app/package.json"] = FileKind.CONFIG
        files["projects/cue-app/src/checkin.ts"] = FileKind.SOURCE
        shape = layout.analyse(index_of(files))
        self.assertIn("projects/cue-app", shape.boundaries)
        self.assertNotIn("projects/cue-app/src/checkin.ts", shape.files)

    def test_a_project_root_with_its_own_source_is_not_a_boundary(self):
        """
        MondayOS's `dashboard/` has a package.json too.

        It is the project's own frontend, and excluding it would lose a real
        capability. The difference is that its parent has source of its own.
        """
        files = source(*[f"dashboard/src/a{i}.tsx" for i in range(6)])
        files["dashboard/package.json"] = FileKind.CONFIG
        files["monday/api.py"] = FileKind.SOURCE
        shape = layout.analyse(index_of(files))
        self.assertEqual(shape.boundaries, frozenset())

    def test_a_project_with_no_nested_projects_has_no_boundary(self):
        shape = layout.analyse(index_of(source("safety/rails.py", "safety/limits.py")))
        self.assertEqual(shape.boundaries, frozenset())


class TestDirectorySignals(unittest.TestCase):
    def test_substantial_directories_are_reported_with_their_count(self):
        shape = layout.analyse(index_of(source(*[f"safety/a{i}.py" for i in range(6)])))
        self.assertEqual(layout.directory_signals(shape)["safety"], ("safety", 6))

    def test_a_small_directory_is_not_a_signal(self):
        shape = layout.analyse(index_of(source("tiny/a.py", "tiny/b.py")))
        self.assertNotIn("tiny", layout.directory_signals(shape))

    def test_signals_are_read_below_the_container(self):
        shape = layout.analyse(
            index_of(
                source(
                    *[f"src/billing/a{i}.py" for i in range(5)],
                    *[f"src/scheduling/b{i}.py" for i in range(5)],
                )
            )
        )
        signals = layout.directory_signals(shape)
        self.assertEqual(set(signals), {"billing", "scheduling"})
        self.assertNotIn("src", signals)

    def test_structural_directories_are_never_signals(self):
        shape = layout.analyse(
            index_of(
                source(
                    *[f"src/models/a{i}.py" for i in range(6)],
                    *[f"src/utils/b{i}.py" for i in range(6)],
                    *[f"src/billing/c{i}.py" for i in range(6)],
                )
            )
        )
        self.assertEqual(set(layout.directory_signals(shape)), {"billing"})

    def test_descent_through_nested_containers_is_bounded(self):
        shape = layout.analyse(
            index_of(
                source(
                    *[f"packages/web/src/billing/a{i}.ts" for i in range(6)],
                    *[f"packages/web/src/checkin/b{i}.ts" for i in range(6)],
                )
            )
        )
        # Whatever it finds, it must stop rather than walk to the leaves.
        self.assertNotIn("a0", layout.directory_signals(shape))


class TestCooccurrence(unittest.TestCase):
    def _app(self) -> layout.Layout:
        """
        A container layout with three real layers.

        `checkin` is spread across pages, components and lib the way a feature
        is. `badge` is three files inside one component directory, the way a UI
        fragment is. The two must not be reported the same.
        """
        return layout.analyse(
            index_of(
                source(
                    "src/pages/checkin.tsx",
                    "src/pages/guests.tsx",
                    "src/pages/comms.tsx",
                    "src/pages/home.tsx",
                    "src/components/checkin/Panel.tsx",
                    "src/components/badge/Badge.tsx",
                    "src/components/badge/BadgeRow.tsx",
                    "src/components/badge/BadgeList.tsx",
                    "src/components/comms/Thread.tsx",
                    "src/lib/checkin.ts",
                    "src/lib/guests.ts",
                    "src/lib/comms.ts",
                    "src/lib/format.ts",
                )
            )
        )

    def test_the_fixture_is_a_container_layout(self):
        # The rest of this class is meaningless if the shape was misread.
        self.assertEqual(self._app().containers, frozenset({"src"}))

    def test_a_name_in_several_layers_is_a_signal(self):
        self.assertIn("checkin", layout.cooccurrence_signals(self._app()))

    def test_a_name_repeated_inside_one_layer_is_not(self):
        """
        Three files named Badge* in `components/` is a UI fragment, not a
        capability. Counting locations rather than layers is what produced
        `panel`, `rail` and `section` as initiatives.
        """
        self.assertNotIn("badge", layout.cooccurrence_signals(self._app()))

    def test_structural_layer_names_are_never_signals(self):
        signals = layout.cooccurrence_signals(self._app())
        for name in ("component", "page", "lib", "src"):
            self.assertNotIn(name, signals)

    def test_a_compound_stem_contributes_its_subject(self):
        """`billing_handler.py` in the handlers layer is evidence for billing."""
        shape = layout.analyse(
            index_of(
                source(
                    "source/billing/charge.py",
                    "source/billing/invoice.py",
                    "source/billing/refund.py",
                    "source/billing/ledger.py",
                    "source/handlers/billing_handler.py",
                    "source/handlers/webhook_handler.py",
                    "source/handlers/queue_handler.py",
                    "source/handlers/retry_handler.py",
                )
            )
        )
        self.assertEqual(shape.containers, frozenset({"source"}))
        self.assertIn("billing", layout.cooccurrence_signals(shape))

    def test_the_spelling_the_project_uses_is_preserved(self):
        """`comms` is what the team calls it; "Comm" is not a name anyone knows."""
        surface, _ = layout.cooccurrence_signals(self._app())["comm"]
        self.assertEqual(surface, "comms")


class TestWorkTokens(unittest.TestCase):
    def test_tokens_come_from_paths_and_stems(self):
        shape = layout.analyse(index_of(source("reasoning/engine.py", "growth/content_item.py")))
        tokens = layout.work_tokens(shape)
        self.assertIn("engine", tokens)
        self.assertIn("content", tokens)
        self.assertIn("reasoning", tokens)

    def test_documentation_contributes_nothing(self):
        shape = layout.analyse(
            index_of(
                {"docs/ROADMAP.md": FileKind.DOCUMENTATION, "safety/rails.py": FileKind.SOURCE}
            )
        )
        self.assertNotIn("roadmap", layout.work_tokens(shape))


if __name__ == "__main__":
    unittest.main()
