# Autonomous Build Workflow

`Monday.build()` is MondayOS's artifact-bound coding workflow. It turns one
existing MondayOS task into an independently reviewed GitHub pull request. It is
separate from the advisory agent-team workflow and does not require a human
approval before it creates the commit, branch, or pull request.

## What happens

```text
task
  → tool and authentication preflight
  → exact remote-base commit
  → isolated, job-owned Git worktree
  → Claude Code patch (bounded-context Codex fallback)
  → fixed controller validation
  → exact staged-artifact fingerprint
  → fresh sterile ChatGPT/Codex review of the exact staged diff
      needs changes → restore exact base → replacement patch (maximum three attempts)
      high-confidence pass → commit → non-force push → pull request
      block/failure → stop without pushing
```

Monday, not the coding model, owns every mutation. A builder can inspect the
repository and return a structured unified patch, but it cannot edit the
worktree, choose validation commands, commit, push, open a pull request, merge,
or deploy. The controller validates and applies the patch itself.

The final reviewer is always a new Codex CLI session authenticated through
ChatGPT. The controller first captures and bounds the exact staged diff and
computes its SHA-256 digest. The reviewer independently verifies that digest,
then runs read-only with model tools disabled from a private temporary directory
that is neither the repository nor the delivery worktree. It receives only the
task objective, exact staged diff, digest, and validation evidence supplied in
the prompt. Candidate-controlled output is withheld on both success and failure
so it cannot encode source text or become model instructions; repair models
receive only the controller-derived check name and exit code. Tracked project
configuration cannot alter the session.
A pass is valid only with high confidence. Monday recomputes the artifact
fingerprint immediately before committing; one changed byte makes the review
stale and stops delivery.

## Current scope

This first implementation intentionally builds only the trusted MondayOS
repository configured as `project_root`. Registered external project delivery,
background workers, automatic merge, and deployment are later increments.

The workflow:

- creates a unique `codex/...` branch from `origin`'s exact default-branch SHA;
- never edits the operator's current checkout;
- permits text source changes only and blocks secrets, keys, Git internals,
  dependency manifests, CI workflows, service/deployment files, and AI/controller
  instruction files;
- uses fixed argument arrays rather than shell commands;
- runs Codex builders and reviewers outside the repository with MCP, shell,
  browser, plugin, skill, hook, worktree, and host-automation features disabled;
- runs `git diff --cached --check` with a fixed, root-owned Apple Git binary and
  runs the repository's sandbox-safe Python test suite with a controller-owned
  empty pytest configuration;
- scans added lines for high-confidence credential patterns;
- retries implementation or repair at most three times, restoring the verified
  job-owned worktree to the exact base before each retry so every proposal is a
  complete replacement rather than a cumulative patch;
- disables commit/push hooks and commit signing for the controller-owned action;
- pushes without force and opens or reconciles one pull request;
- never merges or deploys.

On success the task moves to `REVIEW` to represent an open GitHub pull request.
That status is not a request for the old team-workflow approval command.

## Validation containment

The controller enters macOS Seatbelt through the system sandbox library before
it imports pytest or any candidate-controlled module. The staged worktree is
read-only, both source and worktree Git metadata are hidden from tests, and the
only writable location is a private runtime directory. Network access, Mach
service lookup, process inspection, and the alternate `/System/Volumes/Data`
path into host data are denied.

Child programs may use ordinary fork/exec because many repository tests create
temporary Git repositories. The sandbox denies `posix_spawn`, `setsid`, and
`setpgid` at the kernel boundary so descendants cannot leave the
controller-owned process group. Python subprocesses are pinned to the already
verified interpreter engine and forced through fork/exec. The controller kills
the complete group after each check or deadline.

Validation also applies finite process, open-file, output-file, CPU, address
space, and core limits before candidate code loads. A candidate receives at most
16 additional processes, 128 open files per process, a 16 MiB per-file ceiling,
120 CPU seconds, and 2 GiB of virtual-address headroom above the trusted Python
engine's measured baseline. The outer controller independently caps the whole
process group at 1 GiB of resident memory and the private runtime at 256 MiB and
20,000 entries. Runtime accounting stops at the first exceeded limit, uses
no-follow directory handles, and also watches filesystem free-space movement so
open-but-unlinked files remain visible. A synchronous final scan closes
fast-exit races, and monitoring fails closed if it cannot continue.

Pytest always receives `-c /dev/null`, `--noconftest`, disabled third-party
plugin autoloading, and the explicit `tests` path. Candidate pytest and tox
configuration files, including any `conftest.py`, are protected paths and
cannot be proposed. The contained run explicitly records and deselects only
tests that require capabilities the profile intentionally denies:

- live localhost socket checks;
- inspection of the source repository's real Git history;
- direct signaling of child process groups;
- tests of the outer sandbox boundary itself; and
- the controller's host process-table memory-watchdog test.

The ordinary development test run still executes these tests outside candidate
containment. Every contained exclusion and its reason is retained in validation
evidence for the independent reviewer and pull request.

## Prerequisites

The build workflow uses existing subscription logins rather than Claude or
OpenAI API keys:

1. Install Codex CLI and sign in with ChatGPT. This is mandatory for final review.
2. Optionally install and sign in to Claude Code. When available it is the first
   implementation builder; otherwise Monday uses the ChatGPT-authenticated Codex
   CLI as the builder.
3. Install GitHub CLI and authenticate it for `github.com`.
4. Ensure `origin` points to the intended GitHub repository and its default
   branch is configured.
5. Install MondayOS's development dependencies in `.venv`.
6. Run the delivery worker on the Mac mini. This first containment profile loads
   macOS Seatbelt through the system sandbox library and fails closed on
   unsupported systems.

Check readiness without editing anything:

```bash
monday build capabilities
```

DeepSeek remains available to MondayOS's reasoning/provider layer, but it is not
a tool-capable coding executor in this increment.

## CLI

```bash
monday build run TASK-0001
monday build run TASK-0001 --id delivery-my-stable-id
monday build get delivery-my-stable-id
monday build history --task TASK-0001
monday build capabilities
```

Use `--json` on any build command for machine-readable output. A caller-supplied
delivery ID is idempotent and permanently bound to one task.

## Telegram

Live builds are opt-in and private-chat-only. Add this to the Git-ignored `.env`:

```text
MONDAYOS_TELEGRAM_LIVE_BUILD=true
```

With the flag enabled, private plain text and `/build REQUEST` create a task and
run the autonomous workflow. `/deliver TASK-ID` runs an existing task. Monday
sends phase updates and the final PR link. Allowlisted group `/build` commands
continue to use the advisory team workflow; groups can never start terminal code
execution.

The Telegram implementation is synchronous in this increment. The bot cannot
poll for another update while a build is running; a durable background queue is
the next scaling step.

## Records and GitHub truth

Approved source, the commit, branch, pull request, validation summary, reviewer
identity/verdict, and reviewed artifact digest are stored on GitHub. Detailed
restart-safe controller records stay locally under `logs/delivery/` and are
gitignored because they contain machine paths and in-progress state. A crashed
identity is terminalized as interrupted rather than replaying an uncertain
mutation; a new request receives a new identity. These records are not
credentials, but they are not yet synchronized to GitHub. Do not describe this
increment as storing every runtime byte in GitHub.

Failed worktrees are retained for diagnosis. Earlier rejected candidates are
removed only when another attempt is about to begin; if the attempt budget is
exhausted, the final failed staged candidate remains intact for inspection.
Successful worktrees are removed after the pull request opens while their branch
and commit remain on GitHub.

## Python API

```python
from monday import Monday

monday = Monday()
result = monday.build("run", task_id="TASK-0001")

result.status       # "pr-open" on success
result.pr_url
result.commit_sha
result.attempts
```

Other actions are `get`, `history`, and `capabilities`. Existing
`Monday.execute()`, `Monday.agent()`, and `Monday.team()` behavior is unchanged.
