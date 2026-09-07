"""
Tests for identity allocation.

Two facts drive the design, and the tests are organised around proving each.

**Sequential ids cannot be globally unique across disconnected allocators.** Two
branches diverging from the same counter hold identical state, and a
deterministic allocator on identical state produces identical output. The
branch-divergence tests below demonstrate this rather than assume it — including
the case where the design does *not* prevent a duplicate, only guarantees it will
surface.

**Git detects duplicates, but only for records it tracks.** That asymmetry, not
importance, is why two identity policies exist.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from core.sequence import (
    MAX_SUFFIX_ATTEMPTS,
    SUFFIX_LENGTH,
    SUFFIX_SPACE,
    IdentityPolicy,
    Namespace,
    SequenceAllocator,
    SequenceCollisionError,
    SequenceLockTimeoutError,
    random_suffix,
)


def _tracked(root: Path, prefix: str = "TASK") -> Namespace:
    (root / "records").mkdir(parents=True, exist_ok=True)
    return Namespace(
        prefix=prefix,
        records=root / "records",
        counter=root / ".sequences.json",
        policy=IdentityPolicy.TRACKED_SEQUENTIAL,
    )


def _runtime(root: Path, prefix: str = "RES") -> Namespace:
    (root / "records").mkdir(parents=True, exist_ok=True)
    return Namespace(
        prefix=prefix,
        records=root / "records",
        counter=root / ".sequences.json",
        policy=IdentityPolicy.RUNTIME_HYBRID,
    )


def _shallow_runtime(root: Path, prefix: str = "RES") -> Namespace:
    """
    A runtime namespace that scans shallowly.

    Used to plant a record the high-water scan cannot see but the existence guard
    can — which is how a same-sequence suffix collision is reproduced
    deterministically. Without it, planting the record simply advances the
    sequence and the collision never occurs.
    """
    (root / "records").mkdir(parents=True, exist_ok=True)
    return Namespace(
        prefix=prefix,
        records=root / "records",
        counter=root / ".sequences.json",
        policy=IdentityPolicy.RUNTIME_HYBRID,
        recursive=False,
    )


def _write(ns: Namespace, identifier: str) -> None:
    (ns.records / f"{identifier}.md").write_text(f"# {identifier}\n")


class TestCounterAuthority(unittest.TestCase):
    """
    The counter is a monotonic hint. Disk proves an id is taken; it cannot prove
    one is free, because deletion is silent and references outlive records.
    """

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_stale_counter_heals_from_disk(self):
        ns = _tracked(self.root)
        (self.root / ".sequences.json").write_text(json.dumps({"TASK": 135}))
        _write(ns, "TASK-0138")
        self.assertEqual(SequenceAllocator(ns).allocate(), "TASK-0139")

    def test_a_counter_ahead_of_disk_is_never_repaired_downward(self):
        """
        The decisive case. RES has 34 records but a high-water of 138 — 104 were
        deleted, and 32 of those ids are still cited by tracked documentation.
        Repairing the counter down to the highest surviving record would reissue
        ids those references point at.
        """
        ns = _tracked(self.root)
        (self.root / ".sequences.json").write_text(json.dumps({"TASK": 200}))
        _write(ns, "TASK-0138")
        self.assertEqual(SequenceAllocator(ns).allocate(), "TASK-0201")
        self.assertEqual(json.loads((self.root / ".sequences.json").read_text())["TASK"], 201)

    def test_a_missing_counter_derives_from_disk(self):
        ns = _tracked(self.root)
        _write(ns, "TASK-0042")
        self.assertEqual(SequenceAllocator(ns).allocate(), "TASK-0043")

    def test_a_malformed_counter_is_treated_as_absent(self):
        for junk in ("{ not json", "[]", '{"TASK": "abc"}', '{"TASK": -5}', ""):
            with self.subTest(junk=junk):
                with TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    ns = _tracked(root)
                    (root / ".sequences.json").write_text(junk)
                    _write(ns, "TASK-0007")
                    self.assertEqual(SequenceAllocator(ns).allocate(), "TASK-0008")

    def test_a_deleted_record_never_has_its_id_reissued(self):
        ns = _tracked(self.root)
        allocator = SequenceAllocator(ns)
        first = allocator.allocate()
        _write(ns, first)
        (ns.records / f"{first}.md").unlink()
        self.assertNotEqual(allocator.allocate(), first)

    def test_allocating_preserves_sibling_prefixes_in_the_same_file(self):
        """knowledge/.sequences.json holds five prefixes; one must not erase another."""
        (self.root / ".sequences.json").write_text(json.dumps({"DEC": 11, "RES": 138}))
        ns = _runtime(self.root, "RES")
        SequenceAllocator(ns).allocate()
        data = json.loads((self.root / ".sequences.json").read_text())
        self.assertEqual(data["DEC"], 11)
        self.assertEqual(data["RES"], 139)

    def test_peek_does_not_allocate(self):
        ns = _tracked(self.root)
        allocator = SequenceAllocator(ns)
        self.assertEqual(allocator.peek(), 0)
        self.assertEqual(allocator.peek(), 0)
        allocator.allocate()
        self.assertEqual(allocator.peek(), 1)


class TestIdentityPolicies(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_tracked_ids_stay_short_and_sequential(self):
        ns = _tracked(self.root)
        allocator = SequenceAllocator(ns)
        self.assertEqual(
            [allocator.allocate() for _ in range(3)],
            ["TASK-0001", "TASK-0002", "TASK-0003"],
        )

    def test_runtime_ids_carry_a_suffix(self):
        ns = _runtime(self.root)
        identifier = SequenceAllocator(ns).allocate()
        prefix, sequence, suffix = identifier.split("-")
        self.assertEqual(prefix, "RES")
        self.assertEqual(sequence, "0001")
        self.assertEqual(len(suffix), SUFFIX_LENGTH)

    def test_the_suffix_space_is_large_enough_to_quantify(self):
        """
        Coordination-free probabilistic uniqueness. 32**8 = 2**40 suffixes, so a
        contested allocation collides with probability ~9e-13.
        """
        self.assertEqual(SUFFIX_SPACE, 32**8)
        self.assertGreater(SUFFIX_SPACE, 10**12)

    def test_suffixes_are_drawn_from_an_unambiguous_alphabet(self):
        """No i, l, o or u: the only time anyone reads a suffix is to compare two."""
        drawn = "".join(random_suffix() for _ in range(200))
        for confusable in "ilou":
            self.assertNotIn(confusable, drawn)

    def test_both_id_forms_parse(self):
        """
        Historical ids are never rewritten. RES-0138 predates the hybrid policy
        and must keep resolving forever.
        """
        ns = _runtime(self.root)
        self.assertEqual(ns.parse("RES-0138"), 138)
        self.assertEqual(ns.parse("RES-0139-k3f2m8qp"), 139)
        self.assertIsNone(ns.parse("RES-nope"))
        self.assertIsNone(ns.parse("OTHER-0001"))

    def test_a_historical_unsuffixed_record_still_sets_the_high_water_mark(self):
        ns = _runtime(self.root)
        _write(ns, "RES-0138")
        self.assertTrue(SequenceAllocator(ns).allocate().startswith("RES-0139-"))

    def test_hyphenated_filenames_are_not_misparsed(self):
        """
        Regression against the old workspace allocator, which parsed ids with
        split("-")[-1] and would read CONV-0012-backup as 12... or as garbage.
        """
        ns = _runtime(self.root, "CONV")
        self.assertIsNone(ns.parse("CONV-0012-backup"))
        self.assertEqual(ns.parse("CONV-0012-k3f2m8qp"), 12)


class TestCollisionHandling(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_runtime_suffix_collision_retries(self):
        """
        Two independent allocators forced to draw the same suffix for the same
        sequence — the RNG-collision case, made deterministic.

        The first draw lands on an id that already exists locally, so the
        allocator must notice and draw again rather than return a duplicate.
        """
        ns = _shallow_runtime(self.root)
        (self.root / ".sequences.json").write_text(json.dumps({"RES": 138}))
        # The id the next allocation will compute, already taken by "another
        # branch" that drew the same suffix. Filed where the shallow high-water
        # scan cannot see it, so the sequence still lands on 139.
        nested = self.root / "records" / "archive"
        nested.mkdir(parents=True, exist_ok=True)
        (nested / "RES-0139-aaaaaaaa.md").write_text("from another allocator")

        draws = iter(["aaaaaaaa", "bbbbbbbb"])
        allocator = SequenceAllocator(ns, suffix_source=lambda: next(draws))
        self.assertEqual(allocator.allocate(), "RES-0139-bbbbbbbb")

    def test_a_degenerate_suffix_generator_fails_loudly(self):
        """
        A bounded retry. At 2**40 suffixes a repeat means the generator is broken,
        and looping forever on that is worse than failing.
        """
        ns = _shallow_runtime(self.root)
        (self.root / ".sequences.json").write_text(json.dumps({"RES": 138}))
        nested = self.root / "records" / "archive"
        nested.mkdir(parents=True, exist_ok=True)
        (nested / "RES-0139-aaaaaaaa.md").write_text("x")

        allocator = SequenceAllocator(ns, suffix_source=lambda: "aaaaaaaa")
        with self.assertRaises(SequenceCollisionError) as caught:
            allocator.allocate()
        self.assertIn(str(MAX_SUFFIX_ATTEMPTS), str(caught.exception))

    def test_the_retry_is_bounded_not_infinite(self):
        calls = {"n": 0}

        def degenerate() -> str:
            calls["n"] += 1
            return "aaaaaaaa"

        ns = _shallow_runtime(self.root)
        (self.root / ".sequences.json").write_text(json.dumps({"RES": 1}))
        nested = self.root / "records" / "archive"
        nested.mkdir(parents=True, exist_ok=True)
        (nested / "RES-0002-aaaaaaaa.md").write_text("x")
        with self.assertRaises(SequenceCollisionError):
            SequenceAllocator(ns, suffix_source=degenerate).allocate()
        self.assertEqual(calls["n"], MAX_SUFFIX_ATTEMPTS)

    def test_a_tracked_collision_fails_loudly_and_does_not_skip_ahead(self):
        """
        Deliberately fatal, and deliberately not a retry.

        Allocation runs under the lock with the high-water mark just computed, so
        a pre-existing tracked record at that id means something outside this
        allocator wrote it. Silently advancing to the next number would hide a
        coordination problem rather than report it.

        Reproduced by filing a record where the namespace's own scan does not
        look — a shallow namespace with a record in a subdirectory — which is
        exactly the shape a misfiled or externally-written record takes.
        """
        shallow = Namespace(
            prefix="TASK",
            records=self.root / "records",
            counter=self.root / ".sequences.json",
            policy=IdentityPolicy.TRACKED_SEQUENTIAL,
            recursive=False,
        )
        (self.root / "records").mkdir(parents=True, exist_ok=True)
        nested = self.root / "records" / "archive"
        nested.mkdir(parents=True, exist_ok=True)
        (nested / "TASK-0001.md").write_text("written by something else")

        with self.assertRaises(SequenceCollisionError) as caught:
            SequenceAllocator(shallow).allocate()
        self.assertIn("TASK-0001", str(caught.exception))
        self.assertIn("outside this allocator", str(caught.exception))

    def test_a_tracked_collision_does_not_advance_the_counter(self):
        """Failing loudly must not consume the id it refused to issue."""
        shallow = Namespace(
            prefix="TASK",
            records=self.root / "records",
            counter=self.root / ".sequences.json",
            policy=IdentityPolicy.TRACKED_SEQUENTIAL,
            recursive=False,
        )
        (self.root / "records" / "archive").mkdir(parents=True, exist_ok=True)
        (self.root / "records" / "archive" / "TASK-0001.md").write_text("x")
        with self.assertRaises(SequenceCollisionError):
            SequenceAllocator(shallow).allocate()
        self.assertFalse((self.root / ".sequences.json").exists())


class TestCrashSemantics(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_crash_after_allocation_leaves_a_gap_never_a_reuse(self):
        """
        Record writes happen outside the lock, so this window is real and
        intentional. Gaps are free; reuse is not.
        """
        ns = _tracked(self.root)
        allocator = SequenceAllocator(ns)
        allocated = allocator.allocate()  # caller "crashes" before writing
        self.assertEqual(allocated, "TASK-0001")
        self.assertFalse((ns.records / "TASK-0001.md").exists())
        self.assertEqual(allocator.allocate(), "TASK-0002")

    def test_the_counter_survives_a_restart(self):
        ns = _tracked(self.root)
        SequenceAllocator(ns).allocate()
        # A brand-new allocator, as a restarted process would build.
        self.assertEqual(SequenceAllocator(_tracked(self.root)).allocate(), "TASK-0002")

    def test_the_counter_file_is_written_atomically(self):
        from unittest import mock

        ns = _tracked(self.root)
        SequenceAllocator(ns).allocate()
        original = (self.root / ".sequences.json").read_text()
        with mock.patch("os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                SequenceAllocator(ns).allocate()
        self.assertEqual((self.root / ".sequences.json").read_text(), original)


class TestLocking(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_the_lock_file_is_separate_from_the_counter(self):
        """Locking must never truncate the data it guards."""
        ns = _tracked(self.root)
        self.assertNotEqual(ns.lock_path, ns.counter)
        SequenceAllocator(ns).allocate()
        self.assertTrue(ns.counter.is_file())

    def test_the_lock_is_scoped_to_the_counter_file_not_the_prefix(self):
        """
        Prefixes sharing a counter must share its lock.

        knowledge/.sequences.json holds DEC, DOC, PAT, RES and SPR. Separate
        locks would let two of them read-modify-write the same JSON concurrently
        and lose one's update — the exact race locking exists to prevent.
        """
        shared_counter = self.root / ".sequences.json"
        dec = Namespace("DEC", self.root / "r", shared_counter, IdentityPolicy.TRACKED_SEQUENTIAL)
        res = Namespace("RES", self.root / "r", shared_counter, IdentityPolicy.RUNTIME_HYBRID)
        self.assertEqual(dec.lock_path, res.lock_path)

    def test_separate_counter_files_get_separate_locks(self):
        """Tasks and conversations are independent and must not block each other."""
        tasks = Namespace(
            "TASK",
            self.root / "t",
            self.root / "t" / ".sequences.json",
            IdentityPolicy.TRACKED_SEQUENTIAL,
        )
        conv = Namespace(
            "CONV",
            self.root / "c",
            self.root / "c" / ".sequences.json",
            IdentityPolicy.RUNTIME_HYBRID,
        )
        self.assertNotEqual(tasks.lock_path, conv.lock_path)

    def test_a_held_lock_times_out_rather_than_hanging(self):
        import fcntl

        ns = _tracked(self.root)
        ns.lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = os.open(str(ns.lock_path), os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            allocator = SequenceAllocator(ns)
            allocator._locked().__class__  # noqa: B018 - keep the import honest
            from core.sequence import _NamespaceLock

            lock = _NamespaceLock(ns.lock_path, timeout=0.05)
            with self.assertRaises(SequenceLockTimeoutError):
                lock.__enter__()
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
            os.close(handle)


_WORKER = """
import sys, json
from pathlib import Path
sys.path.insert(0, {repo!r})
from core.sequence import Namespace, SequenceAllocator, IdentityPolicy

root = Path(sys.argv[1]); policy = sys.argv[2]; count = int(sys.argv[3])
ns = Namespace(
    prefix="ID",
    records=root / "records",
    counter=root / ".sequences.json",
    policy=IdentityPolicy[policy],
)
allocator = SequenceAllocator(ns)
out = []
for _ in range(count):
    identifier = allocator.allocate()
    (ns.records / (identifier + ".md")).write_text("x")
    out.append(identifier)
print(json.dumps(out))
"""


class TestConcurrentProcesses(unittest.TestCase):
    """
    Real subprocesses, not threads. The GIL would serialise threads and hide
    exactly the read-modify-write race this locking exists to prevent.
    """

    def _run(self, policy: str, workers: int = 4, each: int = 10) -> list[str]:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "records").mkdir(parents=True)
            script = root / "worker.py"
            script.write_text(_WORKER.format(repo=str(Path(__file__).resolve().parent.parent)))
            procs = [
                subprocess.Popen(
                    [sys.executable, str(script), str(root), policy, str(each)],
                    stdout=subprocess.PIPE,
                    text=True,
                )
                for _ in range(workers)
            ]
            ids: list[str] = []
            for p in procs:
                out, _ = p.communicate(timeout=120)
                self.assertEqual(p.returncode, 0, out)
                ids.extend(json.loads(out))
            return ids

    def test_tracked_allocation_across_processes_never_duplicates(self):
        ids = self._run("TRACKED_SEQUENTIAL")
        self.assertEqual(len(ids), len(set(ids)))
        numbers = sorted(int(i.split("-")[1]) for i in ids)
        self.assertEqual(numbers, list(range(1, len(ids) + 1)), "sequence has holes")

    def test_runtime_allocation_across_processes_never_duplicates(self):
        ids = self._run("RUNTIME_HYBRID")
        self.assertEqual(len(ids), len(set(ids)))


class TestBranchDivergence(unittest.TestCase):
    """
    The guarantee that locking cannot provide.

    Two allocators that diverge from the same counter hold identical state, and a
    deterministic allocator on identical state produces identical output. These
    tests demonstrate that, then show what each policy does about it.
    """

    def _diverge(self, policy: IdentityPolicy, counter: int = 138) -> tuple[str, str]:
        """Two independent clones from one base, each allocating once."""
        out = []
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            for name in ("branch-a", "branch-b"):
                root = base / name
                (root / "records").mkdir(parents=True)
                # Identical starting state: the counter travelled, the ignored
                # records did not.
                (root / ".sequences.json").write_text(json.dumps({"ID": counter}))
                ns = Namespace(
                    prefix="ID",
                    records=root / "records",
                    counter=root / ".sequences.json",
                    policy=policy,
                )
                out.append(SequenceAllocator(ns).allocate())
        return out[0], out[1]

    def test_tracked_sequential_ids_do_collide_across_branches(self):
        """
        Demonstrated, not assumed. This is the arithmetic: identical state in,
        identical id out. The design does not prevent it — git detects it.
        """
        a, b = self._diverge(IdentityPolicy.TRACKED_SEQUENTIAL)
        self.assertEqual(a, b)
        self.assertEqual(a, "ID-0139")

    def test_runtime_hybrid_ids_do_not_collide_across_branches(self):
        """The suffix supplies the uniqueness git cannot."""
        a, b = self._diverge(IdentityPolicy.RUNTIME_HYBRID)
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith("ID-0139-"))
        self.assertTrue(b.startswith("ID-0139-"))

    def test_the_sequential_component_still_agrees_across_branches(self):
        """Readability and ordering survive; only uniqueness is delegated."""
        a, b = self._diverge(IdentityPolicy.RUNTIME_HYBRID)
        self.assertEqual(a.split("-")[1], b.split("-")[1])

    def test_a_fresh_clone_with_a_stale_committed_counter(self):
        """
        MondayOS's live shape: git holds RES=135 while local work reached 138 and
        the records are gitignored. A tracked-sequential namespace reissues; a
        runtime-hybrid one does not.
        """
        with TemporaryDirectory() as tmp:
            origin = Path(tmp) / "origin"
            (origin / "records").mkdir(parents=True)
            (origin / ".sequences.json").write_text(json.dumps({"ID": 138}))
            for n in (136, 137, 138):
                (origin / "records" / f"ID-{n:04d}.md").write_text("x")

            clone = Path(tmp) / "clone"
            (clone / "records").mkdir(parents=True)
            # The clone receives the stale committed counter and no records.
            (clone / ".sequences.json").write_text(json.dumps({"ID": 135}))

            ns = Namespace(
                prefix="ID",
                records=clone / "records",
                counter=clone / ".sequences.json",
                policy=IdentityPolicy.RUNTIME_HYBRID,
            )
            identifier = SequenceAllocator(ns).allocate()
            self.assertTrue(identifier.startswith("ID-0136-"))
            # The sequence repeats, but the full id cannot collide with the
            # origin's ID-0136 because that one has a different suffix.
            self.assertNotEqual(identifier, "ID-0136")
            self.assertFalse((origin / "records" / f"{identifier}.md").exists())

    def test_textual_references_are_not_an_allocation_source(self):
        """
        A test fixture citing ID-9999 must not burn 9,861 ids. Only records and
        the counter are authority.
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ns = _tracked(root, "ID")
            (root / "records" / "notes.md").write_text("see ID-9999 for details")
            self.assertEqual(SequenceAllocator(ns).allocate(), "ID-0001")


if __name__ == "__main__":
    unittest.main()
