"""
The project index — where everything is, and what mentions what.

Three structures, all derived and all rebuildable:

**Files.** One record per indexable file: role, size, digest, symbols, terms,
references. No content is retained; the index says *where* to look, and excerpts
are read from disk when an answer actually cites them.

**A term index.** An inverted map from lowercased identifier or word to the files
containing it. This is what makes "find every place streaming is implemented"
work: it is a search over real content, not over filenames. Terms are extracted
from identifiers, prose and symbol names, with `snake_case` and `camelCase`
split so `render_context` is findable as `render`, `context`, and
`render_context`.

**A symbol index.** Name to definitions, so "where is ContextEngine" resolves to
a file and a line range rather than to every file that mentions it.

The cache is a plain JSON file under the project's own tree, and it is a cache in
the strict sense: it stores nothing that cannot be recovered by re-reading the
project, and deleting it costs a rebuild. Staleness is decided per file by size
and mtime, so an incremental rebuild reparses only what changed.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from intelligence import scanner
from intelligence import symbols as symbol_extraction
from intelligence.models import FileKind, IndexedFile, Symbol, digest_of

# Where the cache lives, relative to the MondayOS root. Gitignored: it is derived
# state about a project, not source.
CACHE_DIR = Path("intelligence") / "cache"

# Schema version. Bumping it invalidates every cache, which is the correct
# response to changing what an entry means.
SCHEMA = 3

# Words too common to discriminate. Short on purpose: an aggressive stoplist
# starts discarding real query terms, and in this codebase "state", "test",
# "build" and "context" are all meaningful.
STOPWORDS: frozenset[str] = frozenset(
    """
    the a an and or but of to in on for with is are was were be been being do does did
    not no if then else when where which who what how this that these those it its as
    at by from into over under out up down all any both each few more most other some
    such only own same than too very can will just should now
    """.split()
)

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z][a-z0-9]*|[a-z0-9]+")

# Cross-references the project makes to its own artefacts.
_REFERENCE = re.compile(r"\b(ADR-\d{3,}|TASK-\d{3,}|DEC-\d{3,}|DOC-\d{3,}|RES-\d{3,})\b")
_PR_REFERENCE = re.compile(r"(?:pull request |PR )#(\d+)", re.I)

MIN_TERM_LENGTH = 3


@dataclass
class IndexStats:
    """What a build did, in numbers a human can sanity-check."""

    files: int = 0
    symbols: int = 0
    terms: int = 0
    reparsed: int = 0
    reused: int = 0
    seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "files": self.files,
            "symbols": self.symbols,
            "terms": self.terms,
            "reparsed": self.reparsed,
            "reused": self.reused,
            "seconds": round(self.seconds, 3),
        }

    def summary(self) -> str:
        return (
            f"{self.files} files, {self.symbols} symbols, {self.terms} terms "
            f"({self.reparsed} parsed, {self.reused} cached) in {self.seconds:.2f}s"
        )


@dataclass
class ProjectIndex:
    """
    Everything the question engine can retrieve from, for one project.

    Scoped to a single project by construction: the root is fixed at build time
    and every path is relative to it. There is no argument that could pull in a
    second project's files, which is the same isolation rule the Context Engine
    holds (ADR-017), applied to a different reader.
    """

    project: str
    root: Path
    files: dict[str, IndexedFile] = field(default_factory=dict)
    # term -> paths containing it
    terms: dict[str, set[str]] = field(default_factory=dict)
    # symbol name (lowercased) -> definitions
    symbols: dict[str, list[Symbol]] = field(default_factory=dict)
    stats: IndexStats = field(default_factory=IndexStats)

    # ------------------------------------------------------------------ query

    def files_of(self, *kinds: FileKind) -> list[IndexedFile]:
        wanted = set(kinds)
        return [f for f in self.files.values() if f.kind in wanted]

    def find_symbol(self, name: str) -> list[Symbol]:
        """Definitions of a name, exact match first, then case-insensitive."""
        return list(self.symbols.get(name.lower(), []))

    def search_symbols(self, fragment: str, limit: int = 20) -> list[Symbol]:
        """
        Definitions whose name contains a fragment.

        Ordered by how closely the name matches — exact, then prefix, then
        substring — and then by path, so results are stable across runs.
        """
        needle = fragment.lower()
        if not needle:
            return []
        hits: list[tuple[int, str, Symbol]] = []
        for key, defs in self.symbols.items():
            if needle not in key:
                continue
            rank = 0 if key == needle else 1 if key.startswith(needle) else 2
            for symbol in defs:
                hits.append((rank, symbol.path, symbol))
        hits.sort(key=lambda h: (h[0], h[1], h[2].line))
        return [h[2] for h in hits[:limit]]

    def files_with(self, term: str) -> set[str]:
        return set(self.terms.get(term.lower(), set()))

    def references_to(self, ref: str) -> list[str]:
        """Files that mention an artefact id such as ADR-017 or TASK-0073."""
        needle = ref.upper()
        return sorted(p for p, f in self.files.items() if needle in f.references)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "project": self.project,
            "root": str(self.root),
            "files": [f.to_dict() for f in self.files.values()],
            "stats": self.stats.to_dict(),
        }


def build(
    project: str,
    root: Path,
    cache_root: Path | None = None,
    use_cache: bool = True,
    now: Callable[[], float] = time.monotonic,
) -> ProjectIndex:
    """
    Build (or incrementally refresh) the index for one project.

    Reuses a cached file entry when its size and mtime are unchanged, so a
    rebuild after editing one file reparses one file. Anything that cannot be
    reused is reparsed; nothing is trusted without checking.
    """
    started = now()
    root = Path(root).resolve()
    cached = _load_cache(project, root, cache_root) if use_cache else {}

    index = ProjectIndex(project=project, root=root)
    stats = index.stats

    for path in scanner.walk(root):
        rel = path.relative_to(root).as_posix()
        try:
            stat = path.stat()
        except OSError:
            continue

        previous = cached.get(rel)
        if previous and previous.size == stat.st_size and previous.mtime == int(stat.st_mtime):
            index.files[rel] = previous
            stats.reused += 1
        else:
            source = scanner.read_text(path)
            if not source:
                continue
            index.files[rel] = _index_file(rel, path, root, source, stat.st_size, stat.st_mtime)
            stats.reparsed += 1

    _build_lookups(index)

    stats.files = len(index.files)
    stats.symbols = sum(len(f.symbols) for f in index.files.values())
    stats.terms = len(index.terms)
    stats.seconds = now() - started

    if use_cache:
        _save_cache(index, cache_root)
    return index


def _index_file(
    rel: str, path: Path, root: Path, source: str, size: int, mtime: float
) -> IndexedFile:
    kind = scanner.classify(path, root)
    return IndexedFile(
        path=rel,
        kind=kind,
        size=size,
        mtime=int(mtime),
        lines=source.count("\n") + 1,
        digest=digest_of(source),
        symbols=symbol_extraction.extract(source, rel),
        terms=extract_terms(source, rel),
        references=extract_references(source),
    )


def extract_terms(source: str, path: str = "") -> set[str]:
    """
    The searchable vocabulary of a file.

    Identifiers are split on both `snake_case` and `camelCase` *and* kept whole,
    so `render_context` is findable three ways. The path contributes too — a
    question about "growth" should reach `growth/service.py` even if the word
    never appears inside it.
    """
    terms: set[str] = set()

    for word in _WORD.findall(source):
        lowered = word.lower()
        if len(lowered) >= MIN_TERM_LENGTH and lowered not in STOPWORDS:
            terms.add(lowered)
        for part in _split_identifier(word):
            if len(part) >= MIN_TERM_LENGTH and part not in STOPWORDS:
                terms.add(part)

    for segment in re.split(r"[/._-]", path.lower()):
        if len(segment) >= MIN_TERM_LENGTH and segment not in STOPWORDS:
            terms.add(segment)

    return terms


def _split_identifier(word: str) -> list[str]:
    parts: list[str] = []
    for chunk in word.split("_"):
        parts.extend(m.group(0).lower() for m in _CAMEL.finditer(chunk))
    return parts


def extract_references(source: str) -> set[str]:
    """Artefact ids this file mentions: ADR-017, TASK-0073, PR #39."""
    refs = {m.group(1).upper() for m in _REFERENCE.finditer(source)}
    refs |= {f"PR#{m.group(1)}" for m in _PR_REFERENCE.finditer(source)}
    return refs


def _build_lookups(index: ProjectIndex) -> None:
    """Invert the per-file data into the term and symbol lookups."""
    for path, entry in index.files.items():
        for term in entry.terms:
            index.terms.setdefault(term, set()).add(path)
        for symbol in entry.symbols:
            index.symbols.setdefault(symbol.name.lower(), []).append(symbol)

    for defs in index.symbols.values():
        defs.sort(key=lambda s: (s.path, s.line))


# --------------------------------------------------------------------------- #
# cache
# --------------------------------------------------------------------------- #


def cache_path(project: str, cache_root: Path | None = None) -> Path:
    base = Path(cache_root) if cache_root else CACHE_DIR
    return base / f"{project}.json"


def _load_cache(project: str, root: Path, cache_root: Path | None) -> dict[str, IndexedFile]:
    """
    Previously indexed files, if the cache is usable.

    Fails toward rebuilding on every uncertainty — wrong schema, different root,
    unreadable file, malformed JSON. A stale index is worse than a slow one: it
    answers confidently about code that no longer exists.
    """
    path = cache_path(project, cache_root)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}

    if payload.get("schema") != SCHEMA or payload.get("root") != str(root):
        return {}

    try:
        return {
            str(entry["path"]): IndexedFile.from_dict(entry) for entry in payload.get("files") or []
        }
    except (KeyError, ValueError, TypeError):
        return {}


def _save_cache(index: ProjectIndex, cache_root: Path | None) -> None:
    """Persist the index. A failure here degrades performance, never correctness."""
    path = cache_path(index.project, cache_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(index.to_dict()), encoding="utf-8")
    except OSError:
        return
