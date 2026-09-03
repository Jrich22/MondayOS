"""
Walking a project and classifying what is in it.

Two jobs: decide which files are worth indexing, and decide what each one *is*.
Both are deterministic and both are cheap — the expensive part (parsing symbols)
happens downstream, only on files that survive this pass.

Classification matters more than it looks. "Is this a decision record or a test"
changes how an answer is assembled: a question about *why* should be answered
from decisions, a question about *where* from source, and a question about
*whether it works* from tests. A single `.py` bucket cannot make that
distinction, so the kinds here are about role, not language.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path

from intelligence.models import FileKind

# Directories that never contain project truth and are expensive to walk.
SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        "dist",
        "build",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".idea",
        ".vscode",
        "htmlcov",
        "coverage",
        "site-packages",
        "egg-info",
        ".egg-info",
        "screenshots",
        "logs",
    }
)

# Extensions worth reading, by role.
SOURCE_EXT: frozenset[str] = frozenset({".py", ".ts", ".tsx", ".js", ".jsx"})
DOC_EXT: frozenset[str] = frozenset({".md", ".markdown", ".rst"})
CONFIG_EXT: frozenset[str] = frozenset({".toml", ".yaml", ".yml", ".json", ".ini", ".cfg"})

# A file bigger than this is almost certainly generated — a lockfile, a bundle, a
# fixture. Indexing it costs time and pollutes the term index with noise.
MAX_FILE_BYTES = 400_000

# Credential-shaped names. The index is read to build prompts, so anything that
# could hold a secret must never enter it — same rule as the Context Engine.
#
# Deliberately conservative: a legitimate source file named `secret.py` or
# `password_rules.ts` is skipped too. That is the correct failure direction — a
# missing file costs a gap in retrieval, an indexed credential costs a
# credential — but it means an absent file is worth checking against this list
# before assuming the indexer is broken.
SECRET_MARKERS: tuple[str, ...] = (
    ".env",
    "secret",
    "credential",
    "password",
    ".pem",
    ".key",
    "id_rsa",
    ".p12",
    ".pfx",
    "keystore",
    ".netrc",
    ".npmrc",
    ".pypirc",
)

# Generated or vendored files that are technically source but are not the
# project's own thinking.
GENERATED_MARKERS: tuple[str, ...] = (
    "package-lock.json",
    "yarn.lock",
    "poetry.lock",
    ".min.js",
    ".min.css",
    "tsconfig.tsbuildinfo",
    ".d.ts",
)

_PROMPT_HINT = re.compile(r"(^|[/_])(prompt|prompts|instruction|instructions)([/_.]|$)", re.I)


def is_secret(path: Path) -> bool:
    name = path.name.lower()
    return any(marker in name for marker in SECRET_MARKERS)


def is_generated(path: Path) -> bool:
    name = path.name.lower()
    return any(marker in name for marker in GENERATED_MARKERS)


def classify(path: Path, root: Path) -> FileKind:
    """
    What role this file plays.

    Order matters: a test that happens to live under `docs/` is still a test, and
    `DECISIONS.md` is a decision record before it is documentation.
    """
    rel = path.relative_to(root)
    parts = {p.lower() for p in rel.parts}
    name = path.name.lower()
    suffix = path.suffix.lower()

    if suffix in DOC_EXT:
        # ADRs are the project's reasoning and are asked about differently from
        # prose documentation — "why did we decide" is a decision question.
        if name in ("decisions.md", "adr.md") or "adr" in parts:
            return FileKind.DECISION
        return FileKind.DOCUMENTATION

    if suffix in SOURCE_EXT:
        if "tests" in parts or "__tests__" in parts:
            return FileKind.TEST
        if name.startswith("test_") or ".test." in name or ".spec." in name:
            return FileKind.TEST
        if _PROMPT_HINT.search(str(rel).replace("\\", "/")):
            return FileKind.PROMPT
        return FileKind.SOURCE

    if suffix in CONFIG_EXT:
        return FileKind.CONFIG

    return FileKind.OTHER


def walk(root: Path) -> Iterator[Path]:
    """
    Every indexable file under a project root.

    Yields in sorted order so an index built twice from the same tree is
    byte-identical — determinism starts here, not at the ranking stage.
    """
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune in place so os.walk does not descend into skipped trees at all.
        dirnames[:] = sorted(
            d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".") or d in (".github",)
        )
        current = Path(dirpath)
        for filename in sorted(filenames):
            path = current / filename
            suffix = path.suffix.lower()
            if suffix not in SOURCE_EXT | DOC_EXT | CONFIG_EXT:
                continue
            if is_secret(path) or is_generated(path):
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            yield path


def read_text(path: Path) -> str:
    """Read a file for indexing. Never raises; an unreadable file is skipped."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
