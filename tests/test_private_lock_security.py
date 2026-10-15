"""Regression tests for descriptor-bound private workflow locks."""

from __future__ import annotations

import os
import stat
import unittest
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import mock

from agents.runtime import _ApprovalLock
from agents.team import _task_lease_name, _TeamIdentityLock, _TeamRunLease
from core.sequence import IdentityPolicy, Namespace, SequenceAllocator
from orchestrator.report import ExecutionReport
from workspace.store import ConversationStore

LockFactory = Callable[[], AbstractContextManager[Any]]


def _lock_cases(root: Path) -> list[tuple[str, LockFactory, Path]]:
    agents = root / "logs" / "agents"
    return [
        ("approval", lambda: _ApprovalLock(root), agents / ".approval.lock"),
        (
            "team-identity",
            lambda: _TeamIdentityLock(root),
            agents / ".team-identity.lock",
        ),
        (
            "team-run",
            lambda: _TeamRunLease(root, "task"),
            agents / "locks" / _task_lease_name("task"),
        ),
    ]


class TestPrivateWorkflowLocks(unittest.TestCase):
    def test_lock_leaf_symlinks_are_rejected(self) -> None:
        for label in ("approval", "team-identity", "team-run"):
            with self.subTest(lock=label), TemporaryDirectory() as tmp:
                root = Path(tmp) / "root"
                outside = Path(tmp) / "outside"
                root.mkdir()
                outside.mkdir()
                cases = {name: (factory, path) for name, factory, path in _lock_cases(root)}
                factory, path = cases[label]
                path.parent.mkdir(parents=True)
                victim = outside / f"{label}.lock"
                path.symlink_to(victim)

                with self.assertRaises(OSError):
                    with factory():
                        pass

                self.assertFalse(victim.exists())
                self.assertTrue(path.is_symlink())

    def test_linked_lock_directories_are_rejected(self) -> None:
        for label in ("approval", "team-identity", "team-run"):
            with self.subTest(lock=label), TemporaryDirectory() as tmp:
                root = Path(tmp) / "root"
                outside = Path(tmp) / "outside"
                root.mkdir()
                outside.mkdir()
                cases = {name: (factory, path) for name, factory, path in _lock_cases(root)}
                factory, path = cases[label]

                if label == "team-run":
                    agents = root / "logs" / "agents"
                    agents.mkdir(parents=True)
                    (agents / "locks").symlink_to(outside, target_is_directory=True)
                else:
                    (root / "logs").mkdir()
                    (root / "logs" / "agents").symlink_to(
                        outside,
                        target_is_directory=True,
                    )

                with self.assertRaises(OSError):
                    with factory():
                        pass

                self.assertEqual(list(outside.iterdir()), [])

    def test_hardlinked_lock_files_are_rejected(self) -> None:
        for label in ("approval", "team-identity", "team-run"):
            with self.subTest(lock=label), TemporaryDirectory() as tmp:
                root = Path(tmp) / "root"
                outside = Path(tmp) / "outside"
                root.mkdir()
                outside.mkdir()
                cases = {name: (factory, path) for name, factory, path in _lock_cases(root)}
                factory, path = cases[label]
                path.parent.mkdir(parents=True)
                victim = outside / f"{label}.lock"
                victim.write_text("external record\n", encoding="utf-8")
                os.link(victim, path)
                before = victim.read_bytes()
                before_mode = stat.S_IMODE(victim.stat().st_mode)

                with self.assertRaises(OSError):
                    with factory():
                        pass

                self.assertEqual(victim.read_bytes(), before)
                self.assertEqual(stat.S_IMODE(victim.stat().st_mode), before_mode)
                self.assertEqual(victim.stat().st_nlink, 2)

    def test_lock_files_and_created_directories_are_private(self) -> None:
        for label in ("approval", "team-identity", "team-run"):
            with self.subTest(lock=label), TemporaryDirectory() as tmp:
                root = Path(tmp) / "root"
                root.mkdir()
                cases = {name: (factory, path) for name, factory, path in _lock_cases(root)}
                factory, path = cases[label]

                previous_umask = os.umask(0)
                try:
                    with factory():
                        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                        self.assertEqual(
                            stat.S_IMODE(path.parent.stat().st_mode),
                            0o700,
                        )
                finally:
                    os.umask(previous_umask)


class TestWorkspaceSequenceLock(unittest.TestCase):
    def test_dangling_sequence_lock_symlink_cannot_create_an_external_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            project_dir = root / "workspace" / "conversations" / "demo"
            project_dir.mkdir(parents=True)
            victim = Path(tmp) / "outside-sequence-lock"
            lock_path = project_dir / ".sequences.json.lock"
            lock_path.symlink_to(victim)

            with self.assertRaises((OSError, ValueError)):
                ConversationStore(root).create("demo", "New conversation")

            self.assertFalse(victim.exists())
            self.assertTrue(lock_path.is_symlink())
            self.assertEqual(list(project_dir.glob("CONV-*.md")), [])

    def test_hardlinked_sequence_lock_preserves_the_external_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            project_dir = root / "workspace" / "conversations" / "demo"
            project_dir.mkdir(parents=True)
            victim = Path(tmp) / "outside-sequence-lock"
            victim.write_text("external lock\n", encoding="utf-8")
            victim.chmod(0o640)
            lock_path = project_dir / ".sequences.json.lock"
            os.link(victim, lock_path)
            before = victim.read_bytes()
            before_mode = stat.S_IMODE(victim.stat().st_mode)
            before_links = victim.stat().st_nlink

            with self.assertRaises((OSError, ValueError)):
                ConversationStore(root).create("demo", "New conversation")

            self.assertEqual(victim.read_bytes(), before)
            self.assertEqual(stat.S_IMODE(victim.stat().st_mode), before_mode)
            self.assertEqual(victim.stat().st_nlink, before_links)
            self.assertEqual(lock_path.stat().st_ino, victim.stat().st_ino)
            self.assertEqual(list(project_dir.glob("CONV-*.md")), [])

    def test_linked_workspace_ancestor_cannot_receive_a_new_conversation(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            outside = Path(tmp) / "outside"
            root.mkdir()
            outside.mkdir()
            (root / "workspace").symlink_to(outside, target_is_directory=True)

            with self.assertRaises((OSError, ValueError)):
                ConversationStore(root).create("demo", "New conversation")

            self.assertEqual(list(outside.iterdir()), [])

    def test_sequence_counter_symlink_preserves_the_external_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            project_dir = root / "workspace" / "conversations" / "demo"
            project_dir.mkdir(parents=True)
            victim = Path(tmp) / "outside-sequences.json"
            victim.write_text('{"CONV": 41}\n', encoding="utf-8")
            victim.chmod(0o640)
            before = victim.read_bytes()
            before_mode = stat.S_IMODE(victim.stat().st_mode)
            before_links = victim.stat().st_nlink
            counter_path = project_dir / ".sequences.json"
            counter_path.symlink_to(victim)

            with self.assertRaises((OSError, ValueError)):
                ConversationStore(root).create("demo", "New conversation")

            self.assertEqual(victim.read_bytes(), before)
            self.assertEqual(stat.S_IMODE(victim.stat().st_mode), before_mode)
            self.assertEqual(victim.stat().st_nlink, before_links)
            self.assertTrue(counter_path.is_symlink())
            self.assertEqual(list(project_dir.glob("CONV-*.md")), [])

    def test_hardlinked_sequence_counter_preserves_the_external_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            project_dir = root / "workspace" / "conversations" / "demo"
            project_dir.mkdir(parents=True)
            victim = Path(tmp) / "outside-sequences.json"
            victim.write_text('{"CONV": 41}\n', encoding="utf-8")
            victim.chmod(0o640)
            counter_path = project_dir / ".sequences.json"
            os.link(victim, counter_path)
            before = victim.read_bytes()
            before_mode = stat.S_IMODE(victim.stat().st_mode)
            before_links = victim.stat().st_nlink

            with self.assertRaises((OSError, ValueError)):
                ConversationStore(root).create("demo", "New conversation")

            self.assertEqual(victim.read_bytes(), before)
            self.assertEqual(stat.S_IMODE(victim.stat().st_mode), before_mode)
            self.assertEqual(victim.stat().st_nlink, before_links)
            self.assertEqual(counter_path.stat().st_ino, victim.stat().st_ino)
            self.assertEqual(list(project_dir.glob("CONV-*.md")), [])

    def test_transient_first_create_race_is_retried_safely(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            root.mkdir()
            real_open = os.open
            transient_failures = 0

            def race_once(
                path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
                flags: int,
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> int:
                nonlocal transient_failures
                if (
                    path == ".sequences.json.lock"
                    and flags & os.O_CREAT
                    and transient_failures == 0
                ):
                    transient_failures += 1
                    raise FileNotFoundError(path)
                return real_open(path, flags, mode, dir_fd=dir_fd)

            with mock.patch("core.secure_io.os.open", side_effect=race_once):
                conversation = ConversationStore(root).create(
                    "demo",
                    "New conversation",
                )

            self.assertEqual(transient_failures, 1)
            self.assertTrue(
                (
                    root
                    / "workspace"
                    / "conversations"
                    / "demo"
                    / f"{conversation.id}.md"
                ).is_file()
            )

    def test_descriptor_scan_failure_does_not_advance_the_counter(self) -> None:
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "project"
            project_dir.mkdir()
            counter = project_dir / ".sequences.json"
            directory_fd = os.open(project_dir, os.O_RDONLY)
            try:
                allocator = SequenceAllocator(
                    Namespace(
                        prefix="CONV",
                        records=project_dir,
                        counter=counter,
                        policy=IdentityPolicy.RUNTIME_HYBRID,
                        recursive=False,
                    ),
                    directory_fd=directory_fd,
                )
                with mock.patch("core.sequence.os.listdir", side_effect=OSError("scan failed")):
                    with self.assertRaises(OSError):
                        allocator.allocate()
            finally:
                os.close(directory_fd)

            self.assertFalse(counter.exists())


class TestExecutionReportTarget(unittest.TestCase):
    def test_hardlinked_report_target_preserves_the_external_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs_dir = root / "executions"
            logs_dir.mkdir()
            victim = root / "outside.json"
            victim.write_text("external record\n", encoding="utf-8")
            victim.chmod(0o640)
            target = logs_dir / "exec-new.json"
            os.link(victim, target)
            before = victim.read_bytes()
            before_mode = stat.S_IMODE(victim.stat().st_mode)
            before_links = victim.stat().st_nlink

            with self.assertRaises(OSError):
                ExecutionReport(
                    execution_id="exec-new",
                    task_id="TASK-0042",
                ).write_log(logs_dir)

            self.assertEqual(victim.read_bytes(), before)
            self.assertEqual(stat.S_IMODE(victim.stat().st_mode), before_mode)
            self.assertEqual(victim.stat().st_nlink, before_links)
            self.assertEqual(target.stat().st_ino, victim.stat().st_ino)


if __name__ == "__main__":
    unittest.main()
