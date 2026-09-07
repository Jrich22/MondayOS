"""
Atomic file writes.

Every store in MondayOS wrote with a bare ``path.write_text(...)``. That call has
two failure modes nobody had accounted for. A crash partway through leaves a
truncated file where a complete one used to be — Python buffers, so a partial
write is not hypothetical. And a reader arriving mid-write sees whatever has
landed so far, which for JSON is usually unparseable and for a conversation file
is a transcript missing its end.

The fix is the standard one, and it works because ``os.replace`` is atomic within
a filesystem: write the new content to a temporary file *in the same directory*,
force it to disk, then swap it into place with a single rename. A reader sees the
old file or the new one. There is no instant at which it sees half of either.

The same-directory requirement is not incidental. ``os.replace`` across
filesystems is a copy, not a rename, and copies are interruptible — which is the
property this module exists to remove.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def write_atomic(
    path: Path,
    data: str | bytes,
    *,
    encoding: str = "utf-8",
    fsync_parent: bool = True,
) -> None:
    """
    Replace ``path`` with ``data``, atomically.

    Guarantees: a concurrent reader observes the complete previous file or the
    complete new one, never a mixture, and never a truncated file. The content is
    on disk before the rename, so a crash immediately after the rename cannot
    leave a file that exists but is empty.

    ``fsync_parent`` additionally forces the *directory entry* to disk, which is
    what makes the rename itself survive power loss rather than merely a process
    crash. It costs one syscall and is worth it for anything that is a record. A
    caller writing a rebuildable cache may turn it off.

    What this does not give you: atomicity *across* files. Two related writes are
    still two writes, and a crash between them leaves the first applied and the
    second not. Callers that care must order their writes so the surviving state
    is safe — which for identity allocation means advancing the counter before
    writing the record, so a crash yields an unused id rather than a reused one.
    """
    path = Path(path)
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)

    payload = data.encode(encoding) if isinstance(data, str) else data

    # Same directory, so the replace below is a rename rather than a copy.
    handle, tmp_name = tempfile.mkstemp(dir=str(directory), prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(handle, "wb") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp, path)
    except BaseException:
        # Never leave the temporary behind. The original file is untouched --
        # nothing has been replaced yet, which is the point of writing aside.
        tmp.unlink(missing_ok=True)
        raise

    if fsync_parent:
        _fsync_directory(directory)


def write_json_atomic(
    path: Path,
    obj: Any,
    *,
    indent: int = 2,
    sort_keys: bool = True,
    fsync_parent: bool = True,
) -> None:
    """
    Serialise ``obj`` as JSON and write it atomically.

    ``sort_keys`` defaults on so a counter file has a stable byte representation.
    Two processes writing the same logical state then produce identical files,
    which keeps diffs and merge conflicts about real changes rather than key
    order.
    """
    text = json.dumps(obj, indent=indent, sort_keys=sort_keys) + "\n"
    write_atomic(path, text, fsync_parent=fsync_parent)


def _fsync_directory(directory: Path) -> None:
    """
    Flush a directory entry.

    Best-effort: some filesystems refuse ``O_RDONLY`` fsync on a directory, and
    on those the rename is still atomic — only its durability across power loss
    is weaker. Failing the whole write over that would trade a real guarantee for
    a theoretical one.
    """
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
