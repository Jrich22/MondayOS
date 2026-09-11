"""
Authoritative answers about a project's own records.

The red-team review found the enforcement could be defeated two ways that were
not parsing problems at all.

**A fabricated SHA verified.** The allowlist resolved abbreviations in both
directions, so any string *beginning* with a real seven-character prefix matched
it -- `5c44663deadbeef...` passed as genuine. Prefix resolution belongs to git,
which rejects both forged and ambiguous revisions, and nothing here reimplements
it.

**Dropped context disabled the check.** Validation consulted only the citations
retrieval had supplied, and `git` is last in the context budget's priority, so a
crowded snapshot silently removed MondayOS's ability to check commits at all.
Budgeting decides what the model is *shown*. It must not decide what MondayOS can
*verify*, and separating those is what this module is for.

Three outcomes, and the third is the one that keeps the system honest:

    RESOLVED     the project's own records contain this identifier
    UNKNOWN      the records were consulted and do not contain it
    UNAVAILABLE  the records could not be consulted at all

`UNAVAILABLE` is never silently treated as clean. A claim MondayOS cannot check
is reported as unverifiable, because "we did not look" and "we looked and it was
fine" are different facts and only one of them is a guarantee.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from core.vcs import RepoScope, git, scope_for


@dataclass(frozen=True)
class TaskRecord:
    """What the authoritative task store says about one identifier."""

    exists: bool
    project: str = ""


# Records a project keeps about itself. Names rather than paths: a project may
# hold its decisions in `docs/DECISIONS.md` or in a `decisions/` directory, and
# both are the same authority.
_DECISION_FILES = ("DECISIONS.md", "ADR.md", "ARCHITECTURE_DECISIONS.md")
_DECISION_DIRS = ("decisions", "adr", "adrs")
_TASK_FILES = ("ACTIVE.md", "BACKLOG.md", "DONE.md", "TASKS.md")
_TASK_DIRS = ("tasks",)

# How a record announces its own identifier: a Markdown heading, a list item, a
# frontmatter field, or a filename. Deliberately anchored -- a decision id
# mentioned in passing inside a paragraph is a reference to the record, not the
# record itself, and only the record is authority.
_ANNOUNCES = r"(?:^|\n)\s{0,3}(?:#{1,6}\s*|[-*+]\s*|id:\s*|\|\s*)?%s\b"

_MAX_RECORD_BYTES = 2_000_000


class Resolution(Enum):
    """Whether a project's own records account for an identifier."""

    RESOLVED = "resolved"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"


class EvidenceAuthority(Protocol):
    """
    What a project can say about its own identifiers, independent of retrieval.

    Every method answers for *this project*: a commit that exists in a parent
    repository but does not touch this project is `UNKNOWN`, not `RESOLVED`. That
    is the same boundary `core.vcs` draws for evidence gathering, applied to
    verification.
    """

    def commit(self, candidate: str) -> Resolution: ...

    def decision(self, identifier: str) -> Resolution: ...

    def task(self, identifier: str) -> Resolution: ...

    def pull_request(self, number: str) -> Resolution: ...

    def path(self, candidate: str) -> Resolution: ...

    def symbol(self, name: str) -> Resolution: ...

    def available(self) -> dict[str, bool]:
        """Which classes this authority can actually answer for."""
        ...


class NullAuthority:
    """
    An authority that can answer nothing.

    Used where no project root is known. Every claim becomes unverifiable rather
    than clean, which is the honest reading: nothing was consulted.
    """

    def commit(self, candidate: str) -> Resolution:
        return Resolution.UNAVAILABLE

    def decision(self, identifier: str) -> Resolution:
        return Resolution.UNAVAILABLE

    def task(self, identifier: str) -> Resolution:
        return Resolution.UNAVAILABLE

    def pull_request(self, number: str) -> Resolution:
        return Resolution.UNAVAILABLE

    def path(self, candidate: str) -> Resolution:
        return Resolution.UNAVAILABLE

    def symbol(self, name: str) -> Resolution:
        return Resolution.UNAVAILABLE

    def available(self) -> dict[str, bool]:
        return dict.fromkeys(
            ("commit", "decision", "task", "pull_request", "path", "symbol"), False
        )


class ProjectAuthority:
    """
    A project answering about its own records.

    Constructed from the project root, so it is scoped the moment it exists and
    cannot be widened by a caller. Reads are cached for the life of one turn:
    validation asks about the same handful of identifiers repeatedly, and a
    conversation should not pay for a repository walk per citation.

    `symbol_index` is injected rather than imported. Without it symbol claims are
    `UNAVAILABLE` -- reported as unverifiable, never as verified. A validator that
    silently passes the class it cannot check is worse than one that admits it.
    """

    def __init__(
        self,
        root: Path,
        slug: str = "",
        symbol_index: Callable[[], object] | None = None,
        task_lookup: Callable[[str], TaskRecord | None] | None = None,
    ) -> None:
        self._root = Path(root)
        # This project's registry slug, which is what a task's recorded owner is
        # compared against. Without it a global store could only answer "does
        # this task exist", and existence is not ownership.
        self._slug = slug
        self._symbol_index = symbol_index
        self._task_lookup = task_lookup
        self._scope: RepoScope | None = None
        self._scoped = False
        self._commits: dict[str, Resolution] = {}
        self._records: dict[str, str | None] = {}
        self._symbols: frozenset[str] | None = None
        self._symbols_loaded = False
        self._tasks: dict[str, TaskRecord | None] = {}

    # ------------------------------------------------------------------ git

    def _repo(self) -> RepoScope:
        if not self._scoped:
            self._scope = scope_for(self._root)
            self._scoped = True
        assert self._scope is not None
        return self._scope

    def commit(self, candidate: str) -> Resolution:
        """
        Whether an abbreviation identifies one commit that touches this project.

        Resolution is git's, not ours. `rev-parse --verify` rejects a revision
        that does not exist *and* one that is ambiguous, which is precisely the
        two failure modes a hand-rolled prefix match got wrong -- it accepted a
        forged extension of a real prefix and had no notion of ambiguity beyond
        the handful of commits retrieval happened to supply.

        A commit that exists in the enclosing repository but does not touch this
        project is `UNKNOWN`. Answering otherwise is how a nested project cites
        its parent's work as its own.
        """
        key = candidate.lower()
        if key in self._commits:
            return self._commits[key]
        self._commits[key] = self._resolve_commit(key)
        return self._commits[key]

    def _resolve_commit(self, candidate: str) -> Resolution:
        scope = self._repo()
        if not scope.available:
            return Resolution.UNAVAILABLE
        # `^{commit}` forces the revision to be a commit rather than a tag or a
        # tree, and `--verify` makes ambiguity an error instead of a guess.
        full = git(
            scope, "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}", pathspec=False
        )
        if not full:
            # An empty result means "no such revision" *or* "git did not answer" --
            # a timeout and a hung repository both look like absence, and
            # reporting a real commit as fabricated is the failure this whole
            # subsystem exists to avoid. One cheap probe separates them.
            if not git(scope, "rev-parse", "--git-dir", pathspec=False):
                return Resolution.UNAVAILABLE
            return Resolution.UNKNOWN
        if not scope.nested:
            return Resolution.RESOLVED
        # `<sha>^!` is that commit alone, excluding its parents, so this asks
        # whether *this* commit touched the project rather than whether some
        # ancestor did.
        touched = git(scope, "log", "--format=%H", "-1", f"{full}^!")
        return Resolution.RESOLVED if touched else Resolution.UNKNOWN

    def pull_request(self, number: str) -> Resolution:
        """
        Whether this project's history records a merge of that pull request.

        The repository is the authority available offline: a merged PR leaves a
        commit saying so. An unmerged or foreign number is `UNKNOWN`, which is
        correct for a claim about what changed in this project.
        """
        scope = self._repo()
        if not scope.available:
            return Resolution.UNAVAILABLE
        subjects = git(scope, "log", "--format=%s", "-n", "500")
        if not subjects:
            return Resolution.UNKNOWN
        wanted = re.compile(rf"pull request\s*#?{re.escape(number)}\b", re.I)
        return Resolution.RESOLVED if wanted.search(subjects) else Resolution.UNKNOWN

    # -------------------------------------------------------------- records

    def decision(self, identifier: str) -> Resolution:
        return self._record("decision", identifier, _DECISION_FILES, _DECISION_DIRS)

    def task(self, identifier: str) -> Resolution:
        """
        Whether this project owns the task being cited.

        Tasks are managed centrally: one store under the MondayOS root holds
        every project's tasks, and each records its owner as a registry slug. So
        the question is not "does TASK-0059 exist" -- it does, for somebody -- but
        "is it this project's". Existence alone would let a Cue App task
        substantiate a claim in sourcingBOT, which is the cross-project leak this
        subsystem exists to prevent, arriving through the front door.

        A task with no recorded owner stays `UNKNOWN` for every project. That is
        the same rule `TaskManager.list_active(project=...)` already applies:
        unknown is not a match, and guessing an owner is how a slug-in-title
        heuristic once assigned tasks to projects that never claimed them.
        """
        if self._task_lookup is not None:
            record = self._lookup_task(identifier)
            if record is None:
                # The store itself could not be consulted. Not "no such task".
                return Resolution.UNAVAILABLE
            if not record.exists:
                return Resolution.UNKNOWN
            if not record.project or not self._slug:
                return Resolution.UNKNOWN
            return (
                Resolution.RESOLVED
                if record.project.lower() == self._slug.lower()
                else Resolution.UNKNOWN
            )
        return self._record("task", identifier, _TASK_FILES, _TASK_DIRS)

    def _lookup_task(self, identifier: str) -> TaskRecord | None:
        if identifier in self._tasks:
            return self._tasks[identifier]
        assert self._task_lookup is not None
        try:
            record = self._task_lookup(identifier)
        except Exception:  # noqa: BLE001 — an unreadable store is unavailable
            record = None
        self._tasks[identifier] = record
        return record

    def _record(
        self,
        kind: str,
        identifier: str,
        filenames: tuple[str, ...],
        dirnames: tuple[str, ...],
    ) -> Resolution:
        """
        Whether a record store announces this identifier.

        A project with no store of that kind returns `UNAVAILABLE` rather than
        `UNKNOWN`: a project that keeps no decision log has not failed to contain
        `ADR-018`, it simply has nothing to be asked.
        """
        corpus = self._record_text(kind, filenames, dirnames)
        if corpus is None:
            return Resolution.UNAVAILABLE
        pattern = re.compile(_ANNOUNCES % _loose(identifier), re.I)
        return Resolution.RESOLVED if pattern.search(corpus) else Resolution.UNKNOWN

    def _record_text(
        self, kind: str, filenames: tuple[str, ...], dirnames: tuple[str, ...]
    ) -> str | None:
        if kind in self._records:
            return self._records[kind]
        parts: list[str] = []
        for directory in (self._root, self._root / "docs"):
            for name in filenames:
                parts.append(_read(directory / name))
        for name in dirnames:
            folder = self._root / name
            if not folder.is_dir():
                continue
            for entry in sorted(folder.rglob("*.md"))[:500]:
                # The filename is itself an announcement: `TASK-0059.md`.
                parts.append(f"\n{entry.stem}\n")
                parts.append(_read(entry))
        corpus = "".join(p for p in parts if p)
        self._records[kind] = corpus or None
        return self._records[kind]

    # ---------------------------------------------------------------- files

    def path(self, candidate: str) -> Resolution:
        """
        Whether a cited path exists inside this project.

        Containment is checked after resolution, so `../other/secrets.py` is
        outside and therefore `UNKNOWN` -- a project may not cite its way out of
        itself any more than it may cite its parent's commits.
        """
        cleaned = candidate.strip().lstrip("./")
        if not cleaned:
            return Resolution.UNKNOWN
        try:
            target = (self._root / cleaned).resolve()
            root = self._root.resolve()
        except (OSError, ValueError):
            return Resolution.UNKNOWN
        if not target.is_relative_to(root):
            return Resolution.UNKNOWN
        return Resolution.RESOLVED if target.exists() else Resolution.UNKNOWN

    # -------------------------------------------------------------- symbols

    def symbol(self, name: str) -> Resolution:
        """
        Whether the project index defines this name.

        `UNAVAILABLE` without an index, and that is reported rather than hidden:
        symbols were previously collected into the evidence set and never checked,
        so a symbols-only answer reported successful validation having validated
        nothing.
        """
        names = self._symbol_names()
        if names is None:
            return Resolution.UNAVAILABLE
        return Resolution.RESOLVED if name.lower() in names else Resolution.UNKNOWN

    def _symbol_names(self) -> frozenset[str] | None:
        if self._symbols_loaded:
            return self._symbols
        self._symbols_loaded = True
        if self._symbol_index is None:
            return None
        try:
            index = self._symbol_index()
            table = getattr(index, "symbols", None)
            if table is None:
                return None
            self._symbols = frozenset(str(k).lower() for k in table)
        except Exception:  # noqa: BLE001 — an unreadable index is unavailable, not empty
            self._symbols = None
        return self._symbols

    # --------------------------------------------------------- availability

    def available(self) -> dict[str, bool]:
        repo = self._repo().available
        return {
            "commit": repo,
            "pull_request": repo,
            "decision": self._record_text("decision", _DECISION_FILES, _DECISION_DIRS) is not None,
            "task": (
                self._lookup_task("TASK-0") is not None
                if self._task_lookup is not None
                else self._record_text("task", _TASK_FILES, _TASK_DIRS) is not None
            ),
            "path": True,
            "symbol": self._symbol_names() is not None,
        }


def _loose(identifier: str) -> str:
    """
    A pattern matching one identifier however the project happens to write it.

    `ADR-003`, `ADR 3` and `adr_3` are one decision. Zero padding is optional on
    both sides for the same reason it is stripped when normalising: padding a
    quotation to a fixed width once turned a real `TASK-0059` into `TASK-059`.
    """
    match = re.match(r"^([A-Za-z]+)[-_ ]?0*(\d+)$", identifier.strip())
    if not match:
        return re.escape(identifier)
    prefix, number = match.groups()
    return rf"{re.escape(prefix)}[-_ ]?0*{number}"


def _read(path: Path) -> str:
    try:
        if not path.is_file() or path.stat().st_size > _MAX_RECORD_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
