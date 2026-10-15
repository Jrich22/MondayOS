"""Small no-follow primitives for private records below a trusted project root."""

from __future__ import annotations

import json
import os
import stat
import time
import uuid
from pathlib import Path
from typing import Any

FileIdentity = tuple[int, int]


class ConcurrentRecordChangeError(OSError):
    """A record changed after it was read but before its migration write."""


class _NoExpectedIdentity:
    pass


_NO_EXPECTED_IDENTITY = _NoExpectedIdentity()


def open_directory_beneath(
    root: Path,
    relative: Path,
    *,
    create: bool = False,
    private: bool = False,
    private_from: int = 0,
) -> int:
    """Open ``relative`` below a trusted root without following child links.

    The supplied root itself is resolved first so normal platform aliases such
    as macOS ``/var -> /private/var`` remain usable. Every project-controlled
    component below it is then opened relative to the preceding directory with
    ``O_NOFOLLOW`` and kept descriptor-bound through the walk.

    When ``private`` is true, existing components at ``private_from`` and below
    are tightened to mode 0700. Newly created components are always mode 0700.
    """
    relative = Path(relative)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("record directory must be a simple relative path")
    if private_from < 0 or private_from > len(relative.parts):
        raise ValueError("private_from must identify a relative path component")
    root_path = Path(root)
    if create:
        root_path.mkdir(parents=True, exist_ok=True)
    trusted_root = root_path.resolve(strict=True)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(trusted_root, flags)
    try:
        for index, component in enumerate(relative.parts):
            try:
                child_fd = os.open(component, flags, dir_fd=directory_fd)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(component, 0o700, dir_fd=directory_fd)
                child_fd = os.open(component, flags, dir_fd=directory_fd)
            opened = os.fstat(child_fd)
            if not stat.S_ISDIR(opened.st_mode):
                os.close(child_fd)
                raise OSError("record path contains a non-directory component")
            if private and index >= private_from:
                os.fchmod(child_fd, 0o700)
            os.close(directory_fd)
            directory_fd = child_fd
        return directory_fd
    except BaseException:
        os.close(directory_fd)
        raise


def read_text_at(
    directory_fd: int,
    name: str,
    *,
    max_bytes: int = 64 * 1024 * 1024,
) -> str:
    """Read one single-link regular UTF-8 file without following links."""
    text, _identity = read_text_with_identity_at(
        directory_fd,
        name,
        max_bytes=max_bytes,
    )
    return text


def read_text_with_identity_at(
    directory_fd: int,
    name: str,
    *,
    max_bytes: int = 64 * 1024 * 1024,
) -> tuple[str, FileIdentity]:
    """Read text and return the stable inode identity that supplied it."""
    _validate_name(name)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(name, flags, dir_fd=directory_fd)
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise OSError("record is not a single-link regular file")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(1024 * 1024, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise OSError("record exceeds the supported size limit")
        current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise OSError("record changed while it was read")
        return (
            b"".join(chunks).decode("utf-8"),
            (opened.st_dev, opened.st_ino),
        )
    finally:
        os.close(fd)


def read_json_at(directory_fd: int, name: str) -> Any:
    """Read and decode one descriptor-bound JSON record."""
    return json.loads(read_text_at(directory_fd, name))


def read_json_with_identity_at(
    directory_fd: int,
    name: str,
) -> tuple[Any, FileIdentity]:
    """Read JSON and return the stable inode identity that supplied it."""
    text, identity = read_text_with_identity_at(directory_fd, name)
    return json.loads(text), identity


def write_text_atomic_at(
    directory_fd: int,
    name: str,
    data: str,
    *,
    expected_identity: FileIdentity | None | _NoExpectedIdentity = _NO_EXPECTED_IDENTITY,
) -> None:
    """Atomically replace one private file in an already-open directory."""
    _validate_name(name)
    original = _replaceable_leaf_identity(directory_fd, name)
    has_expected_identity = not isinstance(expected_identity, _NoExpectedIdentity)
    if has_expected_identity and original != expected_identity:
        raise ConcurrentRecordChangeError("record changed after it was read")
    payload = data.encode("utf-8")
    temp_name = f".{name}.{uuid.uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    temp_fd = os.open(temp_name, flags, 0o600, dir_fd=directory_fd)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(temp_fd, view)
            if written <= 0:
                raise OSError("record write made no progress")
            view = view[written:]
        os.fchmod(temp_fd, 0o600)
        os.fsync(temp_fd)
    except BaseException:
        try:
            os.unlink(temp_name, dir_fd=directory_fd)
        except OSError:
            pass
        raise
    finally:
        os.close(temp_fd)

    try:
        if _replaceable_leaf_identity(directory_fd, name) != original:
            if has_expected_identity:
                raise ConcurrentRecordChangeError(
                    "record changed while its migration was prepared"
                )
            raise OSError("record changed while its replacement was prepared")
        os.replace(
            temp_name,
            name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
    except BaseException:
        try:
            os.unlink(temp_name, dir_fd=directory_fd)
        except OSError:
            pass
        raise
    try:
        os.fsync(directory_fd)
    except OSError:
        pass


def write_json_atomic_at(
    directory_fd: int,
    name: str,
    value: Any,
    *,
    indent: int = 2,
    sort_keys: bool = True,
    expected_identity: FileIdentity | None | _NoExpectedIdentity = _NO_EXPECTED_IDENTITY,
) -> None:
    """JSON variant of :func:`write_text_atomic_at`."""
    payload = json.dumps(value, indent=indent, sort_keys=sort_keys) + "\n"
    write_text_atomic_at(
        directory_fd,
        name,
        payload,
        expected_identity=expected_identity,
    )


def open_private_lock_file(
    root: Path,
    relative_directory: Path,
    name: str,
) -> tuple[int, int]:
    """Open one private lock file below ``root`` without following links.

    The returned tuple is ``(file_fd, directory_fd)``.  Callers must keep the
    directory descriptor open for the full lock lifetime and close both
    descriptors when done.  The visible directory entry is checked both here
    and, via :func:`validate_private_lock_file`, after ``flock`` acquisition so
    a path replacement cannot silently move the lock to a different inode.
    """
    _validate_name(name)
    directory_fd = open_directory_beneath(
        root,
        relative_directory,
        create=True,
        private=True,
    )
    try:
        file_fd = open_private_lock_file_at(directory_fd, name)
        return file_fd, directory_fd
    except BaseException:
        os.close(directory_fd)
        raise


def open_private_lock_file_at(directory_fd: int, name: str) -> int:
    """Open a private lock leaf relative to an already-trusted directory fd."""
    _validate_name(name)
    flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    file_fd: int | None = None
    for attempt in range(16):
        try:
            file_fd = os.open(
                name,
                flags | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=directory_fd,
            )
        except FileExistsError:
            try:
                file_fd = os.open(name, flags, dir_fd=directory_fd)
            except FileNotFoundError:
                file_fd = None
        except FileNotFoundError:
            # Darwin can transiently report ENOENT when several processes race
            # through O_CREAT|O_NOFOLLOW for the same first lock. Exclusive
            # create followed by a bounded reopen retry avoids splitting the
            # lock while still failing closed on persistent path churn.
            file_fd = None
        if file_fd is not None:
            break
        if attempt < 15:
            time.sleep(0.001)
    if file_fd is None:
        raise OSError("lock path did not stabilize during creation")
    try:
        # Validate before chmod so a pre-existing hardlink cannot be used to
        # change the mode of a file outside the project.
        validate_private_lock_file(directory_fd, file_fd, name)
        os.fchmod(file_fd, 0o600)
        validate_private_lock_file(directory_fd, file_fd, name)
        return file_fd
    except BaseException:
        os.close(file_fd)
        raise


def validate_private_lock_file(directory_fd: int, file_fd: int, name: str) -> None:
    """Require a visible single-link regular file to match an open lock fd."""
    _validate_name(name)
    opened = os.fstat(file_fd)
    current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_nlink != 1
        or not stat.S_ISREG(current.st_mode)
        or current.st_nlink != 1
        or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
    ):
        raise OSError("lock path is not a stable single-link regular file")


def _validate_name(name: str) -> None:
    if type(name) is not str or not name or Path(name).name != name:
        raise ValueError("record name must be one plain filename")


def _replaceable_leaf_identity(
    directory_fd: int,
    name: str,
) -> tuple[int, int] | None:
    """Return a safe replacement target's identity, or ``None`` if absent."""
    try:
        current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1:
        raise OSError("record destination is not a single-link regular file")
    return current.st_dev, current.st_ino
