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
  → implementation patch
      MondayOS itself: Claude Code, then bounded-context Codex fallback
      registered external project: bounded-context Codex only
  → fixed controller validation
  → exact staged-artifact fingerprint
  → fresh sterile ChatGPT/Codex review of the exact staged diff
      needs changes → restore exact base → replacement patch (maximum three attempts)
      high-confidence pass → commit → non-force push → pull request
      block/failure → stop without pushing

open pull request
  → exact-SHA-bound, read-only GitHub observation
      open → report pending/passed/failed/no configured checks
      exact reviewed head merged → complete an eligible MondayOS task
      closed without merge → return a task still in REVIEW to IN_PROGRESS
      head changed → fail closed as diverged; return REVIEW to IN_PROGRESS
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

## Supported targets and trust boundary

The workflow can build MondayOS itself or one explicitly enrolled registered
project. MondayOS remains the controller in both cases: tasks, project identity,
delivery records, model configuration, the trusted Python environment, and the
sandbox bootstrap stay under the MondayOS root. An external repository is only
the source target for its isolated worktree, branch, commit, and pull request.
The workflow never starts a second MondayOS instance from the target repository.

Ordinary project registration remains analysis-only. External delivery requires
a one-time registration that names both the local checkout and the exact GitHub
repository it is permitted to target:

```bash
monday project register customer-portal /path/to/customer-portal \
  --delivery-repository acme/customer-portal
```

Use `--overwrite` to replace an existing analysis-only registration with a
delivery-enabled one. The registry stores the canonical local directory, its
filesystem identity, and the normalized `OWNER/REPO` identity. Delivery fails
closed if the directory disappears, becomes a symlink, is replaced, is
ambiguous, or its `origin` no longer identifies that GitHub repository. A target
must also be separate from the MondayOS controller directory and must be a
standalone checkout with its own real `.git` directory. Git linked worktrees are
rejected as delivery targets, and files inside `.git` may not be symbolic or
hard links, so an external build cannot mutate another checkout's Git database.

Each external task is explicitly bound to that registered project, and each
build names the same project:

```bash
monday task create --title "Add account health endpoint" \
  --objective "Expose a read-only service health response." \
  --project customer-portal
monday build capabilities --project customer-portal
monday build run TASK-0001 --project customer-portal
```

The public build surface accepts a registered project name, not a filesystem
path or arbitrary Git remote. A delivery ID is permanently bound to its task,
canonical project, local target identity, and expected GitHub repository; it
cannot be reused to redirect an interrupted or completed delivery.

Publication is guarded by both the task lock and a repository-wide workflow
lease. The push and pull-request creation processes inherit those guards, so a
controller exit cannot let another worker publish concurrently. If an exit
happens after an irreversible operation, retrying the same delivery ID verifies
that the remote branch still equals the approved commit and then reconciles the
exact pull request without pushing twice. A different delivery ID for the same
task and repository is rejected while that state remains unresolved.

External projects use only the controlled-context Codex patch builder. Claude
Code remains available as the first builder for MondayOS self-builds, with Codex
as fallback. Both target types use a fresh, independent ChatGPT/Codex session as
the mandatory final reviewer.

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

Explicit delivery enrollment authorizes the branch push and pull-request side
effect. That push may start CI, preview environments, or other automation that
the target GitHub repository already associates with pushed branches. MondayOS
does not configure, approve, or read logs from those external jobs. Its separate
observer reports only bounded aggregate check state and PR lifecycle, and it
still never merges or deploys.

On success the task moves to `REVIEW` to represent an open GitHub pull request.
That status is not a request for the old team-workflow approval command.
The delivery result remains `pr-open`; later GitHub state is stored separately
as an observation so a failed check cannot rewrite the fact that the reviewed
artifact was delivered. `monday build observe` reads only structured PR and
check metadata. It follows the REST totals for latest check runs and combined
commit statuses, paginating at most 500 combined checks; partial responses,
inconsistent pagination, or a larger result fail closed as unavailable. It
never reads check logs, changes the PR, merges, or deploys.

If a newer PR-backed delivery exists for the same task, any terminal outcome for
an older PR is recorded but does not change the task underneath the newer work.
Otherwise, a verified merge of an exact reviewed head completes an eligible task
idempotently. Completed, cancelled, blocked, retargeted, and otherwise
human-managed task states are not overridden by the observer.

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

### External-project validation limits

Every target receives the fixed staged-diff check. When an external worktree
contains `pyproject.toml`, MondayOS also runs its `tests` directory through the
controller-owned Python and the same containment boundary. It does not execute
the target checkout's `.venv`, sandbox bootstrap, `conftest.py`, or third-party
pytest plugins, and it does not install dependencies. Tests therefore fail if
they require packages that are unavailable in the trusted MondayOS environment.

No repository-specific test command is inferred or accepted from the task or
model. In this increment, a target without `pyproject.toml`—including projects
in other language ecosystems—gets the diff check and independent exact-artifact
review but no automatic project test suite. Existing target CI may run only as
a consequence of the later branch push; its result is not a prerequisite for
MondayOS to open the pull request. The observer can later report its bounded
aggregate result without consuming check names, logs, annotations, or output.

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

Authenticated CLI discovery intentionally ignores the caller's `PATH`. Codex is
accepted from the ChatGPT app bundle, `/opt/homebrew/bin`, `/usr/local/bin`, or
`/usr/bin`; Claude Code and GitHub CLI are accepted from the same Homebrew/system
binary locations. Their resolved executable must remain under a trusted app,
Homebrew, or system installation root. Install a custom copy into one of those
locations rather than adding an arbitrary directory to `PATH`.

Check readiness without editing anything:

```bash
monday build capabilities
```

For an external target, register it with `--delivery-repository OWNER/REPO` and
check that exact target instead:

```bash
monday build capabilities --project customer-portal
```

DeepSeek remains available to MondayOS's reasoning/provider layer, but it is not
a tool-capable coding executor in this increment.

## CLI

```bash
monday build run TASK-0001
monday build run TASK-0001 --id delivery-my-stable-id
monday build get delivery-my-stable-id
monday build observe delivery-my-stable-id
monday build history --task TASK-0001
monday build capabilities

monday build run TASK-0002 --project customer-portal
monday build get delivery-my-stable-id --project customer-portal
monday build observe delivery-my-stable-id --project customer-portal
monday build history --project customer-portal
monday build capabilities --project customer-portal
```

Use `--json` on any build command for machine-readable output. A caller-supplied
delivery ID is idempotent and permanently bound to one task and target.

## Telegram

Live builds are opt-in and private-chat-only. Add this to the Git-ignored `.env`:

```text
MONDAYOS_TELEGRAM_LIVE_BUILD=true
```

With the flag enabled, private plain text and `/build REQUEST` create a task and
run the autonomous workflow against MondayOS. Use
`/build PROJECT :: REQUEST` for an explicitly enrolled external project.
`/deliver TASK-ID` reads an existing task's saved project binding and selects
that target automatically. Monday sends phase updates and the final PR link.
After the request becomes a request-free replay tombstone, Monday keeps
observing that exact PR in the background. It reports only meaningful changes:
checks pending, passed, failed, or absent; merge; unmerged closure; or a head SHA
that no longer matches the ChatGPT-approved commit. Observation outages back off
and retry without blocking Telegram intake or retaining raw GitHub diagnostics.
Allowlisted group `/build` commands continue to use the advisory team workflow;
groups can never start terminal code execution.

Telegram atomically persists each accepted minimal request together with its
advanced offset in `logs/telegram/state.json`, immediately returns a saved
receipt with the FIFO position, and continues polling. One non-daemon worker
exclusively performs Monday/model jobs, including `Monday.build()`, so build
concurrency remains one even while intake stays responsive. Unauthorized and
otherwise ignored content is not queued.

Queued, running, waiting, and retrying work is recovered after restart with
stable Telegram job, task, run, and delivery identities. Unexpected poison work
is bounded to three handler attempts. A controller-classified transient failure
before commit or push instead creates a request-free, deterministic delivery
retry outside the FIFO. It uses increasing delays, at most six delivery runs,
and a 24-hour lifetime; policy, permanent, unclassified, and publication-started
failures never receive a fresh identity. A graceful stop finishes the active
build and leaves later work queued; `monday telegram --once` instead ingests one
poll response and drains its accepted work synchronously before exiting.

## Records and GitHub truth

Approved source, the commit, branch, pull request, validation summary, reviewer
identity/verdict, and reviewed artifact digest are stored on GitHub. Detailed
restart-safe controller records and all delivery worktrees—including external
project worktrees—stay locally under the MondayOS controller's `logs/delivery/`;
Telegram queue state stays locally in `logs/telegram/state.json`. Both are
Git-ignored because they contain machine paths or in-progress state. Terminal
Telegram replay tombstones keep stable identity and outcome data but remove the
active request text. A PR-backed tombstone may also retain the minimal delivery,
chat, PR, next-poll, and last-notified fields needed for restart-safe lifecycle
updates. A transient pre-publication failure may retain a separate request-free
retry checkpoint with the bound task, project, chat, deterministic delivery
identity, attempt number, and due/expiry times. These records are not
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

For a registered target, enroll it once, bind the task, and select that project
for readiness and delivery:

```python
monday.project(
    "register",
    name="customer-portal",
    path="/path/to/customer-portal",
    delivery_repository="acme/customer-portal",
)
task = monday.task(
    "create",
    title="Add account health endpoint",
    objective="Expose a read-only service health response.",
    project="customer-portal",
)
readiness = monday.build("capabilities", project="customer-portal")
result = monday.build(
    "run",
    task_id=task.task_id,
    project="customer-portal",
)
```

Other actions are `get`, `history`, and `capabilities`. Existing
`Monday.execute()`, `Monday.agent()`, and `Monday.team()` behavior is unchanged.
