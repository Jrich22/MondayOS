"""
Context source adapters — the only code that reads a project's world.

Each adapter turns one MondayOS subsystem, or one part of a project's tree, into
a ``ContextSource``. They are deliberately small and deliberately dumb: they
gather facts and format them as lines. No adapter decides what is relevant, no
adapter calls a model, and no adapter writes anything.

Three rules govern every adapter in this module, and they are the reason it is
one file rather than scattered (ADR-017):

**Scoped by construction.** An adapter receives a resolved project and its root
path. There is no argument that could name a second project, so cross-project
leakage is not a bug that careful review prevents — it is a call that cannot be
expressed.

**Fail closed.** Every adapter is wrapped so that any exception becomes an empty
source carrying the error. A subsystem that breaks makes the context thinner,
never wider. Falling back to unscoped data on failure would turn an outage into
a disclosure.

**Never carry secrets.** Files whose names indicate credentials are skipped
outright, and every string produced here goes through ``core.redaction`` before
it can be persisted or sent. The skip is the intent; the redaction assumes the
skip failed.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from core import redaction
from workspace.context import relevance
from workspace.context.snapshot import ContextSource

# Filenames and directories an adapter must never read from. Matched on the
# lowercase name, so `.env.local` and `PRODUCTION.KEY` are both caught.
_SECRET_NAME_MARKERS: tuple[str, ...] = (
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

# Directories that are never worth reading and are expensive to walk.
_SKIP_DIRS: frozenset[str] = frozenset(
    {
        "node_modules",
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        "dist",
        "build",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "secrets",
        "coverage",
        "htmlcov",
    }
)

# How much of any single file is worth reading for context. A whole architecture
# document is not context, it is the thing context points at.
_MAX_FILE_PEEK = 4_000

# Caps on how many items an adapter yields before the budget even sees them, so a
# project with 900 tasks does not build a 900-item list to then discard it.
_MAX_ITEMS = 40


def is_secret_path(path: Path) -> bool:
    """True when a path's name marks it as credential material."""
    name = path.name.lower()
    return any(marker in name for marker in _SECRET_NAME_MARKERS)


def safe_source(
    name: str,
    label: str,
    origin: str,
    build: Callable[[], list[str]],
    query: str = "",
    baseline_reason: str = relevance.REASON_BASELINE,
    pin: Callable[[str], str] | None = None,
) -> ContextSource:
    """
    Run an adapter body, failing closed, and rank what it produced.

    Any exception becomes an empty source carrying the reason. The error text is
    redacted too: an exception message can quote a path or a value, and an
    unredacted traceback is exactly the kind of thing that leaks a token.

    Ranking happens here rather than in each adapter so every source records why
    its items were chosen, without five adapters each implementing attribution
    slightly differently.
    """
    try:
        items = build()
    except Exception as exc:  # noqa: BLE001 — fail closed: thin context, never wide
        return ContextSource(
            name=name,
            label=label,
            origin=origin,
            error=redaction.redact_text(f"{type(exc).__name__}: {exc}"),
        )

    pinned = {i: reason for i, text in enumerate(items) if (reason := (pin(text) if pin else ""))}
    ranked = relevance.rank(items, query, baseline_reason=baseline_reason, priority=pinned)
    texts, reasons = relevance.split(ranked[:_MAX_ITEMS])
    return ContextSource(
        name=name,
        label=label,
        origin=origin,
        items=[redaction.redact_text(i) for i in texts],
        reasons=reasons,
        truncated=len(items) > _MAX_ITEMS,
    )


# --------------------------------------------------------------------------- #
# identity
# --------------------------------------------------------------------------- #


def identity_source(project: str, root: Path, description: str, query: str = "") -> ContextSource:
    """Who this project is. The one source without which nothing else means anything."""

    def build() -> list[str]:
        items = [f"Project: {project}"]
        if description:
            items.append(f"Description: {description}")
        items.append(f"Location: {root}")

        readme = _first_existing(root, ("README.md", "readme.md", "README.markdown"))
        if readme:
            headline = _readme_headline(readme)
            if headline:
                items.append(f"README: {headline}")
        return items

    # Identity is never reordered: every line of it is load-bearing.
    return safe_source("identity", "Project identity", "project registry", build)


# --------------------------------------------------------------------------- #
# docs / ADRs
# --------------------------------------------------------------------------- #


def docs_source(project: str, root: Path, query: str = "") -> ContextSource:
    """
    Architecture decisions and documentation the project already wrote.

    ADR *titles and statuses*, not bodies. The title is the decision; the body is
    the argument for it, and a model that needs the argument can be pointed at the
    file. Sending every ADR body would spend the whole budget on one source.
    """

    def build() -> list[str]:
        docs_dir = root / "docs"
        if not docs_dir.is_dir():
            return []

        items: list[str] = []
        decisions = docs_dir / "DECISIONS.md"
        if decisions.is_file() and not is_secret_path(decisions):
            adrs = _adr_titles(decisions)
            if adrs:
                items.append(f"Architecture decisions on record ({len(adrs)}):")
                items.extend(f"  {a}" for a in adrs)

        names = sorted(
            p.name
            for p in docs_dir.iterdir()
            if p.is_file() and p.suffix.lower() in (".md", ".markdown") and not is_secret_path(p)
        )
        if names:
            items.append(f"Documentation available: {', '.join(names)}")
        return items

    return safe_source(
        "docs",
        "Documentation & ADRs",
        f"{root / 'docs'}",
        build,
        query=query,
        baseline_reason=relevance.REASON_ARCHITECTURE,
    )


def _adr_titles(path: Path) -> list[str]:
    """Extract `## ADR-NNN: Title` headings and the Status line that follows."""
    text = _read_text(path)
    titles: list[str] = []
    current: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## ADR-"):
            if current:
                titles.append(current)
            current = stripped.lstrip("# ").strip()
        elif current and stripped.startswith("**Status:**"):
            status = stripped.replace("**Status:**", "").strip()
            titles.append(f"{current} [{status}]")
            current = None
    if current:
        titles.append(current)
    return titles


# --------------------------------------------------------------------------- #
# tasks
# --------------------------------------------------------------------------- #


def tasks_source(
    project: str, fetch: Callable[[], list[dict[str, Any]]], query: str = ""
) -> ContextSource:
    """
    What is being worked on, from the real TaskManager.

    Takes a *callable* rather than a list so the read itself happens inside
    ``safe_source``. Passing an already-materialised list would run the reader
    outside the fail-closed wrapper, and a TaskManager that raised would take the
    whole snapshot down instead of contributing an empty source — which is
    exactly backwards from ADR-017.

    The callable is already project-scoped by the engine, so this adapter still
    has no way to widen its own scope.
    """

    def build() -> list[str]:
        tasks = fetch()
        if not tasks:
            return []
        items: list[str] = []
        for task in tasks:
            task_id = str(task.get("id", "")).strip()
            title = str(task.get("title", "")).strip()
            status = str(task.get("status", "")).strip()
            priority = str(task.get("priority", "")).strip()
            bits = " ".join(b for b in (f"[{status}]" if status else "", priority) if b)
            items.append(f"{task_id} {bits} {title}".strip())
        return items

    return safe_source("tasks", "Tasks", "TaskManager", build, query=query, pin=_pin_live_task)


def _pin_live_task(text: str) -> str:
    """
    Mark a task that is actively being worked as always-relevant.

    An in-progress or in-review task bears on almost any question about a
    project, whether or not the question happens to use its words. Derived from
    the rendered item rather than re-reading the task list, so the source is
    fetched exactly once and the pin cannot disagree with what was rendered.
    """
    return relevance.REASON_ACTIVE_TASK if ("[in-progress]" in text or "[review]" in text) else ""


# --------------------------------------------------------------------------- #
# knowledge
# --------------------------------------------------------------------------- #


def knowledge_source(
    project: str, fetch: Callable[[], list[dict[str, Any]]], query: str = ""
) -> ContextSource:
    """
    What has been learned and written down, from the existing KnowledgeStore.

    Titles and summaries only. The knowledge body is retrievable on request; what
    context needs is knowing the entry exists.

    Takes a callable for the same fail-closed reason as ``tasks_source``.
    """

    def build() -> list[str]:
        entries = fetch()
        if not entries:
            return []
        items: list[str] = []
        for entry in entries:
            entry_id = str(entry.get("id", "")).strip()
            title = str(entry.get("title", "")).strip()
            entry_type = str(entry.get("type", "")).strip()
            summary = str(entry.get("summary", "")).strip()
            head = f"{entry_id} [{entry_type}] {title}".strip()
            items.append(f"{head} — {summary}" if summary else head)
        return items

    return safe_source(
        "knowledge",
        "Project knowledge",
        "KnowledgeStore",
        build,
        query=query,
        baseline_reason=relevance.REASON_RECENT,
    )


# --------------------------------------------------------------------------- #
# git
# --------------------------------------------------------------------------- #


def git_source(project: str, root: Path, commit_limit: int = 10, query: str = "") -> ContextSource:
    """
    Branch, working-tree state and recent commits for the project's own repo.

    Runs git in the project's directory, so a project that is not a repository
    produces an empty source rather than accidentally reporting MondayOS's own
    state. That distinction matters: reporting the wrong repo's branch is a
    quiet, plausible, completely wrong answer.
    """

    def build() -> list[str]:
        toplevel = git_command(root, "rev-parse", "--show-toplevel")
        if not toplevel:
            return []

        repo = Path(toplevel)
        # A project may be its own repository, or a directory inside a larger one
        # (as the managed products are inside MondayOS). Both are reported, but
        # never conflated: for a nested project every query is scoped to its own
        # path, because reporting the parent repo's whole state as the project's
        # is a plausible, confident, wrong answer.
        nested = repo.resolve() != root.resolve()
        scope = ["--", str(root)] if nested else []

        items: list[str] = []
        if nested:
            items.append(f"Lives inside the {repo.name} repository at {root.name}/")

        branch = git_command(root, "rev-parse", "--abbrev-ref", "HEAD")
        if branch:
            items.append(f"Current branch: {branch}")

        porcelain = git_command(root, "status", "--porcelain", *scope)
        changed = [line for line in porcelain.splitlines() if line.strip()]
        if changed:
            where = "in this project" if nested else ""
            items.append(f"Working tree: {len(changed)} uncommitted change(s) {where}".strip())
            for line in changed[:10]:
                items.append(f"  {line.strip()}")
        else:
            items.append("Working tree: clean")

        log = git_command(root, "log", f"-{commit_limit}", "--format=%h %s", *scope)
        commits = [line for line in log.splitlines() if line.strip()]
        if commits:
            label = "touching this project" if nested else ""
            items.append(f"Recent commits ({len(commits)}) {label}:".replace(" :", ":"))
            items.extend(f"  {c}" for c in commits)
        return items

    return safe_source(
        "git",
        "Git state",
        f"git in {root}",
        build,
        query=query,
        baseline_reason=relevance.REASON_RECENT,
    )


def git_command(root: Path, *args: str) -> str:
    """
    Run one git command in a project directory.

    Timed out and non-raising: a hung or broken repository must degrade context,
    never block a conversation.
    """
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


# --------------------------------------------------------------------------- #
# shared helpers
# --------------------------------------------------------------------------- #


def _first_existing(root: Path, names: tuple[str, ...]) -> Path | None:
    for name in names:
        candidate = root / name
        if candidate.is_file() and not is_secret_path(candidate):
            return candidate
    return None


def _read_text(path: Path) -> str:
    """Read a bounded prefix of a file. Never raises, never reads a secret."""
    if is_secret_path(path):
        return ""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return handle.read(_MAX_FILE_PEEK)
    except OSError:
        return ""


def _readme_headline(path: Path) -> str:
    """The first meaningful prose line of a README — its one-line self-description."""
    for line in _read_text(path).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "!", "[", "---", "<!--", "|", ">")):
            continue
        return stripped[:300]
    return ""
