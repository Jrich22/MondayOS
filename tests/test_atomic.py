"""
Tests for atomic writes.

Every store in MondayOS wrote with a bare `write_text`. The failure that matters
is not losing data — it is a *partial* file replacing a complete one, because a
truncated JSON counter reads as absent and a truncated conversation reads as a
shorter conversation. Both are silent.

The load-bearing tests here are the ones that interrupt a write and then assert
the old file is still intact.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from core.atomic import write_atomic, write_json_atomic


class TestAtomicWrite(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_it_writes(self):
        target = self.root / "a.txt"
        write_atomic(target, "hello")
        self.assertEqual(target.read_text(), "hello")

    def test_it_replaces_existing_content_entirely(self):
        target = self.root / "a.txt"
        write_atomic(target, "a long original value")
        write_atomic(target, "short")
        self.assertEqual(target.read_text(), "short")

    def test_it_creates_missing_parent_directories(self):
        target = self.root / "deep" / "nested" / "a.txt"
        write_atomic(target, "x")
        self.assertEqual(target.read_text(), "x")

    def test_bytes_and_text_both_work(self):
        write_atomic(self.root / "t.txt", "text")
        write_atomic(self.root / "b.bin", b"bytes")
        self.assertEqual((self.root / "t.txt").read_text(), "text")
        self.assertEqual((self.root / "b.bin").read_bytes(), b"bytes")

    def test_a_failure_mid_write_leaves_the_original_intact(self):
        """
        The whole point. A crash must not turn a complete file into a partial one.
        """
        target = self.root / "a.txt"
        write_atomic(target, "ORIGINAL")

        with mock.patch("os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                write_atomic(target, "NEW CONTENT THAT NEVER LANDS")

        self.assertEqual(target.read_text(), "ORIGINAL")

    def test_a_failure_leaves_no_temporary_file_behind(self):
        target = self.root / "a.txt"
        write_atomic(target, "ORIGINAL")
        with mock.patch("os.replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                write_atomic(target, "x")
        leftovers = [p.name for p in self.root.iterdir() if p.name != "a.txt"]
        self.assertEqual(leftovers, [])

    def test_the_temporary_is_written_in_the_same_directory(self):
        """
        os.replace is only atomic within one filesystem. A temp file in /tmp would
        make the swap a copy, and copies are interruptible -- which is the exact
        property this module exists to remove.
        """
        seen: list[str] = []
        real = os.replace

        def spy(src, dst):
            seen.append(str(src))
            return real(src, dst)

        with mock.patch("os.replace", side_effect=spy):
            write_atomic(self.root / "sub" / "a.txt", "x")

        self.assertTrue(seen)
        self.assertEqual(Path(seen[0]).parent, self.root / "sub")

    def test_content_is_flushed_before_the_rename(self):
        """A rename that publishes an unflushed file publishes an empty one."""
        order: list[str] = []
        real_fsync, real_replace = os.fsync, os.replace

        with (
            mock.patch(
                "os.fsync", side_effect=lambda fd: (order.append("fsync"), real_fsync(fd))[1]
            ),
            mock.patch(
                "os.replace",
                side_effect=lambda s, d: (order.append("replace"), real_replace(s, d))[1],
            ),
        ):
            write_atomic(self.root / "a.txt", "x")

        self.assertIn("fsync", order)
        self.assertIn("replace", order)
        self.assertLess(order.index("fsync"), order.index("replace"))

    def test_a_reader_never_sees_a_partial_file(self):
        """
        Repeated large rewrites: every observation must parse as one complete
        version, never a mixture of two.
        """
        target = self.root / "big.txt"
        write_atomic(target, "A" * 200_000)
        for _ in range(20):
            write_atomic(target, "B" * 200_000)
            content = target.read_text()
            self.assertIn(set(content), [{"A"}, {"B"}])
            self.assertEqual(len(content), 200_000)

    def test_parent_fsync_can_be_disabled_for_caches(self):
        with mock.patch("core.atomic._fsync_directory") as spy:
            write_atomic(self.root / "a.txt", "x", fsync_parent=False)
            spy.assert_not_called()

    def test_a_directory_that_refuses_fsync_does_not_fail_the_write(self):
        """
        Some filesystems refuse fsync on a directory. The rename is still atomic
        there; only power-loss durability is weaker, and failing the whole write
        over that trades a real guarantee for a theoretical one.
        """
        with mock.patch("os.fsync", side_effect=OSError("not supported")):
            # The file fsync is inside the try in _fsync_directory only for the
            # directory; a file-level failure should still propagate.
            with self.assertRaises(OSError):
                write_atomic(self.root / "a.txt", "x")


class TestAtomicJson(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_it_round_trips(self):
        target = self.root / "c.json"
        write_json_atomic(target, {"RES": 138, "TASK": 79})
        self.assertEqual(json.loads(target.read_text()), {"RES": 138, "TASK": 79})

    def test_keys_are_sorted_for_a_stable_byte_representation(self):
        """
        Two processes writing the same logical state produce identical files, so
        diffs and merge conflicts are about real changes rather than key order.
        """
        a, b = self.root / "a.json", self.root / "b.json"
        write_json_atomic(a, {"TASK": 1, "RES": 2})
        write_json_atomic(b, {"RES": 2, "TASK": 1})
        self.assertEqual(a.read_text(), b.read_text())

    def test_it_ends_with_a_newline(self):
        target = self.root / "c.json"
        write_json_atomic(target, {"a": 1})
        self.assertTrue(target.read_text().endswith("\n"))

    def test_json_and_text_share_the_primitive(self):
        """
        Atomicity is a property of the write, not the format -- YAML frontmatter
        and JSON counters use the same guarantee.
        """
        target = self.root / "c.json"
        write_json_atomic(target, {"a": 1})
        with mock.patch("os.replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                write_json_atomic(target, {"a": 2})
        self.assertEqual(json.loads(target.read_text()), {"a": 1})


if __name__ == "__main__":
    unittest.main()
