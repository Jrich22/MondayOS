"""Atomic delivery-job storage and identity reservation."""
from __future__ import annotations

import fcntl
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

from core.atomic import write_json_atomic
from delivery.types import DeliveryJob

_DELIVERY_ID = re.compile(r"^delivery-[A-Za-z0-9-]{1,80}$")


class DeliveryStore:
    """Persist delivery records under the controller's ignored runtime tree."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root).resolve(strict=True)
        if not self.project_root.is_dir():
            raise ValueError("delivery project root must be a directory")
        self.root = self.project_root / "logs" / "delivery"
        self.records = self.root / "runs"
        self._ensure_runtime_tree()

    def reserve(self, job: DeliveryJob) -> DeliveryJob | None:
        """Create one identity, or return its existing exact record."""
        self._validate_id(job.delivery_id)
        self._ensure_runtime_tree()
        lock_path = self.root / ".identity.lock"
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(lock_path, flags, 0o600)
        with os.fdopen(descriptor, "a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                path = self._path(job.delivery_id)
                if path.exists() or path.is_symlink():
                    return self.get(job.delivery_id)
                self.persist(job)
                return None
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def persist(self, job: DeliveryJob) -> None:
        self._validate_id(job.delivery_id)
        self._ensure_runtime_tree()
        path = self._path(job.delivery_id)
        if path.is_symlink():
            raise ValueError("delivery record path must not be a symlink")
        write_json_atomic(path, job.to_dict())

    def get(self, delivery_id: str) -> DeliveryJob:
        self._validate_id(delivery_id)
        self._ensure_runtime_tree()
        path = self._path(delivery_id)
        if path.is_symlink():
            raise ValueError("delivery record path must not be a symlink")
        if not path.exists():
            raise FileNotFoundError(f"No delivery job {delivery_id!r}")
        try:
            payload: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Delivery record {path.name!r} is unreadable: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Delivery record {path.name!r} must be an object")
        job = DeliveryJob.from_dict(payload)
        if job.delivery_id != delivery_id:
            raise ValueError(
                f"Delivery record {path.name!r} identifies itself as "
                f"{job.delivery_id!r}"
            )
        return job

    def history(self, *, task_id: str | None = None, limit: int = 20) -> list[DeliveryJob]:
        self._ensure_runtime_tree()
        jobs: list[DeliveryJob] = []
        for path in sorted(self.records.glob("delivery-*.json")):
            jobs.append(self.get(path.stem))
        if task_id is not None:
            jobs = [job for job in jobs if job.task_id == task_id]
        jobs.sort(key=lambda job: job.created_at, reverse=True)
        return jobs[: max(0, limit)] if limit else jobs

    def _path(self, delivery_id: str) -> Path:
        return self.records / f"{delivery_id}.json"

    def _ensure_runtime_tree(self) -> None:
        """Create the private runtime tree without ever traversing a symlink."""
        parent = self.project_root
        logs_root = self.project_root / "logs"
        for path in (logs_root, self.root, self.records):
            try:
                metadata = path.lstat()
            except FileNotFoundError:
                try:
                    path.mkdir(mode=0o700)
                except FileExistsError:
                    # Another process may have won the creation race.  The
                    # lstat below still verifies what it created.
                    pass
                metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise ValueError(f"delivery runtime path must be a real directory: {path}")
            if path != logs_root and stat.S_IMODE(metadata.st_mode) & 0o077:
                raise ValueError(f"delivery runtime directory must be owner-only: {path}")
            try:
                if path.resolve(strict=True).parent != parent.resolve(strict=True):
                    raise ValueError("delivery runtime escaped the configured project root")
            except RuntimeError as exc:
                raise ValueError("delivery runtime contains a symlink loop") from exc
            parent = path

    @staticmethod
    def _validate_id(delivery_id: str) -> None:
        if not isinstance(delivery_id, str) or not _DELIVERY_ID.fullmatch(delivery_id):
            raise ValueError(
                "delivery_id must begin with 'delivery-' and contain only "
                "letters, digits, or hyphens (maximum 89 characters)"
            )
