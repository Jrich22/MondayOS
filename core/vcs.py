"""
The boundary between a project and the repository that contains it.

Git resolves upward. Run `git log` inside `projects/cue-app` and git walks to the
enclosing MondayOS repository and answers about *that* — so asking Cue App what
changed recently returned MondayOS's commits, with a citation list identical to
MondayOS's own. The answer was confident, plausible, and about the wrong project.

That is a correctness problem rather than a presentation one, and it is fixed
here rather than in a prompt: a nested project's history reads are scoped with a
pathspec, so the parent's commits are **not retrieved**. There is nothing for a
narrator to be told to ignore, because it never arrives.

The distinction this module exists to hold:

    project root   the directory a project occupies
    git toplevel   the repository that happens to contain it

For a standalone repository they are the same path and nothing changes. For a
nested project they differ, and every history read must say which one it means.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

# History reads are bounded: a hung or enormous repository must degrade evidence,
# never block a conversation.
DEFAULT_TIMEOUT = 15.0


@dataclass(frozen=True)
class RepoScope:
    """
    Where a project sits relative to version control.

    ``nested`` is the field that matters. It is false for a standalone repository
    — WeatherBot, MondayOS itself — and true for a project living inside a larger
    one, which is where unscoped reads go wrong.
    """

    root: Path
    toplevel: Path | None = None

    @property
    def available(self) -> bool:
        """Whether this project has any version control at all."""
        return self.toplevel is not None

    @property
    def nested(self) -> bool:
        """Whether the project is a subdirectory of a larger repository."""
        return self.toplevel is not None and self.toplevel != self.root

    @property
    def pathspec(self) -> list[str]:
        """
        The `-- <path>` arguments that confine a history read to this project.

        Empty for a standalone repository, where the whole repo *is* the project.
        """
        return ["--", str(self.root)] if self.nested else []

    def describe(self) -> str:
        """A line stating the boundary, for evidence that should show its scope."""
        if not self.available:
            return f"{self.root.name} is not under version control"
        if self.nested and self.toplevel is not None:
            return (
                f"{self.root.name} lives inside the {self.toplevel.name} repository; "
                "history is scoped to this project's own path"
            )
        return f"{self.root.name} is its own repository"


def scope_for(root: Path, timeout: float = DEFAULT_TIMEOUT) -> RepoScope:
    """
    Resolve where a project sits relative to git.

    A directory with no repository yields an unavailable scope rather than an
    error: a project that is not version-controlled simply has no history, which
    is a fact about it rather than a failure to report.
    """
    resolved = Path(root).resolve()
    out = _run(resolved, ["rev-parse", "--show-toplevel"], timeout)
    if not out:
        return RepoScope(root=resolved, toplevel=None)
    try:
        toplevel = Path(out.strip()).resolve()
    except OSError:
        return RepoScope(root=resolved, toplevel=None)
    return RepoScope(root=resolved, toplevel=toplevel)


def git(
    scope: RepoScope,
    *args: str,
    pathspec: bool = True,
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    """
    Run one git command for a project, confined to that project.

    ``pathspec`` appends ``-- <project root>`` for a nested project, which is what
    makes the parent repository's activity unavailable rather than merely
    unmentioned. Pass ``pathspec=False`` for commands where a path argument is
    meaningless or would change the meaning — ``rev-parse``, ``symbolic-ref`` —
    and note that those return repository-wide answers by nature: a nested
    project shares its parent's branch, and saying so is accurate.

    Non-raising and bounded, like every other evidence read: a broken repository
    yields no history rather than taking down the answer.
    """
    if not scope.available:
        return ""
    argv = [*args, *(scope.pathspec if pathspec else [])]
    return _run(scope.root, argv, timeout)


def _run(cwd: Path, args: list[str], timeout: float) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""
