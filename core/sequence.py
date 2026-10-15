"""
Identity allocation — one policy for how MondayOS names the things it creates.

Two facts shape everything here, and they are worth stating before any code.

**Globally monotonic sequential ids cannot be guaranteed across disconnected
allocators without shared coordination.** Two branches that diverge from the same
counter hold identical state; a deterministic allocator applied to identical
state produces identical output. `flock` cannot see another machine and atomic
writes make a write durable, not unique. This is arithmetic, not a limitation of
the implementation, and the only escapes are coordination (which costs offline
operation), partitioning the space, or accepting probabilistic uniqueness.

**Git provides a collision-detection point, but only for records it tracks.** Two
branches that each write `tasks/active/TASK-0080.md` produce an add/add conflict
on merge — the ambiguity surfaces exactly when the two histories first meet. Two
branches that each write an ignored `knowledge/runtime/research/RES-0139.md`
never meet at all, so nothing surfaces.

That asymmetry, not importance or size, is why there are two identity policies.

    TRACKED_SEQUENTIAL   records enter git -> sequential ids; local uniqueness
                         from locking, distributed collisions detected at merge
    RUNTIME_HYBRID       records never enter git -> sequential prefix for
                         readability plus a random suffix for uniqueness

The counter in both cases is a **monotonic high-water hint**, never authority.
Disk proves an id is taken; it cannot prove one is free, because deletion is
silent and references outlive records. The counter remembers ids that were issued
and later deleted — information disk cannot reconstruct. So allocation takes the
maximum of the two and never repairs downward.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from core.atomic import write_json_atomic
from core.secure_io import (
    open_private_lock_file_at,
    read_json_at,
    validate_private_lock_file,
    write_json_atomic_at,
)

# Crockford-style lowercase base32 without the ambiguous letters i, l, o, u.
# Readable when a human has to compare two ids by eye, which is the only time
# anyone reads a suffix at all.
_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"

# Eight characters over a 32-symbol alphabet is 32**8 = 2**40 distinct suffixes,
# about 1.1 x 10^12. Two allocators that independently reach the same sequence
# number collide only if they also draw the same suffix, so the practical
# collision probability per contested allocation is ~9 x 10^-13.
SUFFIX_LENGTH = 8
SUFFIX_SPACE = len(_ALPHABET) ** SUFFIX_LENGTH

# How many suffixes to try before giving up. A retry means the random draw hit an
# id that already exists locally, which at this suffix space should never happen;
# looping forever on a broken generator would be worse than failing.
MAX_SUFFIX_ATTEMPTS = 8

# How long to wait for another process's allocation before failing. Allocation
# holds the lock for a directory scan, so this is generous by three orders of
# magnitude. An indefinite hang is worse than a clear error.
LOCK_TIMEOUT_SECONDS = 5.0
_LOCK_POLL_SECONDS = 0.01


class IdentityPolicy(Enum):
    """
    How a namespace guarantees uniqueness.

    A first-class MondayOS policy rather than an allocator detail: which one a
    namespace uses is a statement about whether its records are ever exchanged
    between machines, and that decision belongs in the type system where a reader
    of the namespace declaration can see it.
    """

    # Records are committed to git. Sequential ids stay short and sortable;
    # cross-branch duplicates are possible but always surface as an add/add
    # merge conflict on the record path.
    TRACKED_SEQUENTIAL = "tracked_sequential"

    # Records are gitignored, so no merge ever compares them and a duplicate
    # would be permanently invisible. The sequence stays for readability and
    # ordering; a random suffix supplies the uniqueness git cannot.
    RUNTIME_HYBRID = "runtime_hybrid"


class SequenceCollisionError(RuntimeError):
    """
    A unique id could not be produced.

    Raised when a runtime namespace draws colliding suffixes repeatedly, or when
    a tracked namespace allocates an id whose record already exists. The second
    case is deliberately fatal: allocation happens under a lock, so a pre-existing
    tracked record means something outside this allocator wrote it, and silently
    skipping to the next number would hide a coordination problem rather than
    report it.
    """


class SequenceLockTimeoutError(RuntimeError):
    """Another process held the namespace lock for too long."""


@dataclass(frozen=True)
class Namespace:
    """
    One id space, and everything needed to allocate in it safely.

    Bound once where a store is constructed rather than passed as loose arguments
    at each call site. The four private allocators this replaces diverged exactly
    there: two scanned recursively and two did not, and one parsed ids by
    splitting on hyphens, so `CONV-0012-backup.md` read as 12.
    """

    prefix: str
    # Directory scanned for existing records, to recover the on-disk high-water.
    records: Path
    # JSON file holding the monotonic hint. Its lock lives beside it.
    counter: Path
    policy: IdentityPolicy
    recursive: bool = True
    width: int = 4

    @property
    def lock_path(self) -> Path:
        """
        The lock guarding this namespace's counter.

        Derived from the counter path, not the prefix, so prefixes that share a
        counter file share its lock. `knowledge/.sequences.json` holds DEC, DOC,
        PAT, RES and SPR; giving them separate locks would let two of them
        read-modify-write the same JSON concurrently and lose one's update. The
        cost is that a DEC allocation briefly blocks a RES allocation, which is a
        directory scan, not a model call.

        A separate file from the counter, so locking never truncates the data it
        guards.
        """
        return self.counter.with_name(self.counter.name + ".lock")

    def format(self, sequence: int, suffix: str = "") -> str:
        core = f"{self.prefix}-{sequence:0{self.width}d}"
        return f"{core}-{suffix}" if suffix else core

    def parse(self, stem: str) -> int | None:
        """
        The sequence number in a record filename, or None.

        Accepts both forms. `RES-0138` predates the hybrid policy and is still a
        valid id; `RES-0139-k3f2m8qp` is what a runtime namespace issues now.
        Historical ids are never rewritten, so both must parse forever.
        """
        match = re.match(rf"^{re.escape(self.prefix)}-(\d+)(?:-([{_ALPHABET}]+))?$", stem)
        return int(match.group(1)) if match else None


def random_suffix(length: int = SUFFIX_LENGTH) -> str:
    """A cryptographically random suffix. Separate so tests can force collisions."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


class SequenceAllocator:
    """
    Allocates ids for one namespace.

    The lock covers allocation only — read the counter, scan disk, compute, write
    the counter — and is released before the caller writes the record. A record
    write can take arbitrarily long, and holding the lock across it would
    serialise the whole system behind its slowest writer.

    The consequence is deliberate: a crash between allocation and record write
    leaves a **gap** in the sequence. Gaps are free. Reuse is not.
    """

    def __init__(
        self,
        namespace: Namespace,
        suffix_source: Callable[[], str] = random_suffix,
        *,
        directory_fd: int | None = None,
    ) -> None:
        """Bind one namespace.

        ``directory_fd`` is borrowed, not duplicated. The caller must keep it
        open for every allocator operation. Descriptor-bound allocation is
        intentionally limited to a flat namespace whose counter shares the
        records directory.
        """
        self._ns = namespace
        # Injected so a test can force two allocators to draw the same suffix.
        self._suffix_source = suffix_source
        self._directory_fd = directory_fd
        if directory_fd is not None:
            if namespace.recursive:
                raise ValueError(
                    "descriptor-bound allocation requires a flat namespace"
                )
            if namespace.counter.parent != namespace.records:
                raise ValueError(
                    "descriptor-bound allocation requires the counter and records "
                    "to share one directory"
                )

    @property
    def namespace(self) -> Namespace:
        return self._ns

    # ------------------------------------------------------------- inspection

    def highest_on_disk(self) -> int:
        """
        The largest sequence number among existing records.

        Reads filenames rather than contents: the filename is the record of which
        ids are taken, so a record whose body is unreadable still reserves its id.
        """
        if self._directory_fd is not None:
            names = os.listdir(self._directory_fd)
            highest = 0
            for name in names:
                number = self._ns.parse(Path(name).stem)
                if number is not None:
                    highest = max(highest, number)
            return highest

        directory = self._ns.records
        if not directory.is_dir():
            return 0
        try:
            paths = (
                directory.rglob(f"{self._ns.prefix}-*")
                if self._ns.recursive
                else directory.glob(f"{self._ns.prefix}-*")
            )
            highest = 0
            for path in paths:
                number = self._ns.parse(path.stem)
                if number is not None:
                    highest = max(highest, number)
            return highest
        except OSError:
            return 0

    def peek(self) -> int:
        """The current high-water mark, without allocating."""
        return max(self._read_counter(), self.highest_on_disk())

    # ------------------------------------------------------------- allocation

    def allocate(self) -> str:
        """
        Reserve and return the next id for this namespace.

        The sequence is `max(counter, highest_on_disk) + 1`. The counter is only
        ever raised, never repaired downward: a counter ahead of disk is the
        normal state after any deletion, and lowering it would reissue ids that
        deleted records still hold references to.
        """
        with self._locked():
            sequence = max(self._read_counter(), self.highest_on_disk()) + 1

            if self._ns.policy is IdentityPolicy.TRACKED_SEQUENTIAL:
                identifier = self._ns.format(sequence)
                if self._record_exists(identifier):
                    # Under the lock with the high-water mark just computed, this
                    # cannot happen from our own allocation. Something else wrote
                    # it, and skipping ahead would hide that.
                    raise SequenceCollisionError(
                        f"{identifier} already exists on disk although allocation "
                        f"computed it as the next free id in {self._ns.prefix}. "
                        "Something outside this allocator wrote that record."
                    )
            else:
                identifier = self._allocate_hybrid(sequence)

            self._write_counter(sequence)
            return identifier

    def _allocate_hybrid(self, sequence: int) -> str:
        """
        A runtime id: sequence for readability, random suffix for uniqueness.

        Retries on a local collision. At 2**40 suffixes this should never fire,
        so a retry means the generator is degenerate rather than unlucky -- which
        is exactly when a bounded loop matters.
        """
        for _ in range(MAX_SUFFIX_ATTEMPTS):
            candidate = self._ns.format(sequence, self._suffix_source())
            if not self._record_exists(candidate):
                return candidate
        raise SequenceCollisionError(
            f"could not find an unused suffix for {self._ns.prefix}-{sequence:0{self._ns.width}d} "
            f"after {MAX_SUFFIX_ATTEMPTS} attempts; the suffix generator is not "
            "producing distinct values."
        )

    def _record_exists(self, identifier: str) -> bool:
        """
        Whether a record with this exact id exists anywhere beneath the namespace.

        Always recursive, even when the namespace scans shallowly for its
        high-water mark. The two answer different questions: `highest_on_disk`
        reports where the namespace files its records, while this is a safety
        guard, and a guard that only looks where it expects trouble is not one.
        A record filed in an unexpected subdirectory still holds its id.
        """
        if self._directory_fd is not None:
            return any(
                name.startswith(f"{identifier}.")
                for name in os.listdir(self._directory_fd)
            )

        directory = self._ns.records
        if not directory.is_dir():
            return False
        try:
            return any(True for _ in directory.rglob(f"{identifier}.*"))
        except OSError:
            return False

    # ---------------------------------------------------------------- counter

    def _read_counter(self) -> int:
        """
        The stored hint, or 0.

        Missing, truncated, non-JSON, wrong-type, and negative values are treated
        as absent. Descriptor-bound mode deliberately propagates filesystem
        safety errors so an unsafe counter leaf cannot be replaced or ignored.
        """
        if self._directory_fd is not None:
            try:
                data = read_json_at(self._directory_fd, self._ns.counter.name)
            except FileNotFoundError:
                return 0
            except (UnicodeError, json.JSONDecodeError):
                return 0
        else:
            try:
                data = json.loads(self._ns.counter.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                return 0
        if not isinstance(data, dict):
            return 0
        value = data.get(self._ns.prefix, 0)
        try:
            number = int(value)
        except (TypeError, ValueError):
            return 0
        return max(0, number)

    def _write_counter(self, sequence: int) -> None:
        """Persist the hint, preserving other prefixes in the same file."""
        if self._directory_fd is not None:
            try:
                data = read_json_at(self._directory_fd, self._ns.counter.name)
            except FileNotFoundError:
                data = {}
            except (UnicodeError, json.JSONDecodeError):
                data = {}
        else:
            try:
                data = json.loads(self._ns.counter.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                data = {}
        if not isinstance(data, dict):
            data = {}
        # Never lower a sibling prefix, and never lower our own.
        existing = data.get(self._ns.prefix, 0)
        try:
            existing = int(existing)
        except (TypeError, ValueError):
            existing = 0
        data[self._ns.prefix] = max(existing, sequence)
        if self._directory_fd is not None:
            write_json_atomic_at(self._directory_fd, self._ns.counter.name, data)
        else:
            write_json_atomic(self._ns.counter, data)

    # ------------------------------------------------------------------- lock

    def _locked(self) -> _NamespaceLock:
        return _NamespaceLock(
            self._ns.lock_path,
            directory_fd=self._directory_fd,
        )


class _NamespaceLock:
    """
    An exclusive lock over one counter file's allocations.

    `fcntl.flock` rather than a lock directory: the kernel releases it when the
    holder dies, so a crash mid-allocation cannot leave a lock that a human has to
    clear. That is the whole reason not to use `mkdir`.

    Stdlib rather than `portalocker`: MondayOS is POSIX-only in practice, and the
    dependency buys Windows support nobody needs.
    """

    def __init__(
        self,
        path: Path,
        timeout: float = LOCK_TIMEOUT_SECONDS,
        *,
        directory_fd: int | None = None,
    ) -> None:
        self._path = path
        self._timeout = timeout
        self._handle: int | None = None
        self._directory_fd = directory_fd
        self._owned_directory_fd: int | None = None

    def __enter__(self) -> _NamespaceLock:
        active_directory_fd = self._directory_fd
        if active_directory_fd is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            active_directory_fd = os.open(self._path.parent, flags)
            self._owned_directory_fd = active_directory_fd
        try:
            self._handle = open_private_lock_file_at(
                active_directory_fd,
                self._path.name,
            )
        except BaseException:
            self._close_owned_directory()
            raise
        deadline = time.monotonic() + self._timeout
        while True:
            try:
                fcntl.flock(self._handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                validate_private_lock_file(
                    active_directory_fd,
                    self._handle,
                    self._path.name,
                )
                return self
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(self._handle)
                    self._handle = None
                    self._close_owned_directory()
                    raise SequenceLockTimeoutError(
                        f"another process held {self._path} for more than {self._timeout}s"
                    ) from None
                time.sleep(_LOCK_POLL_SECONDS)
            except BaseException:
                try:
                    fcntl.flock(self._handle, fcntl.LOCK_UN)
                finally:
                    os.close(self._handle)
                    self._handle = None
                    self._close_owned_directory()
                raise

    def __exit__(self, *_exc: object) -> None:
        if self._handle is not None:
            try:
                fcntl.flock(self._handle, fcntl.LOCK_UN)
            finally:
                os.close(self._handle)
                self._handle = None
                self._close_owned_directory()

    def _close_owned_directory(self) -> None:
        if self._owned_directory_fd is not None:
            os.close(self._owned_directory_fd)
            self._owned_directory_fd = None
