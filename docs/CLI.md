# MondayOS CLI

**Specification v1.0 — Sprint 1.5**

The `monday` command is the canonical interface for humans, Claude Code, and future automation to interact with MondayOS.

---

## Installation

```bash
pip install -e .        # install from source (editable)
```

After installation, the `monday` binary is available in your environment.

---

## Design principles

- **No business logic in the CLI.** Every command calls the public `Monday` API. The CLI is a thin translation layer: arguments in, formatted text out.
- **No module bypass.** No internal class (`KnowledgeStore`, `TaskManager`, `ReasoningEngine`) is imported directly. All calls go through `Monday()`.
- **Pipe-safe output.** No ANSI color codes. Plain text with Unicode separators. Output is safe to pipe, redirect, or parse.
- **Explicit controller root.** The `--project-root` flag controls where MondayOS tasks, knowledge, registry data, and delivery records are persisted. It does not select an external delivery target; use a registered project name for that.

---

## Global flags

```
monday [--project-root PATH] <command> [options]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--project-root PATH` | `.` | Path to the MondayOS project root directory. |

Global flags must appear **before** the subcommand name:

```bash
monday --project-root /path/to/project status
```

---

## Commands

### `monday status`

Show the system health and module initialization status.

```
monday status
```

Example output:
```
MondayOS v0.1.0
Session : a3f8c2d1-...
Uptime  : 0.01s

Status  : healthy

Modules:
  ok    brain
  ok    events
  ok    knowledge
  ok    memory
  ok    search
  ok    tasks
```

---

### `monday ask`

Answer an engineering question using internal knowledge — no external model calls.

```
monday ask "<prompt>"
```

The reasoning engine classifies the question, searches the knowledge base and active tasks, traverses entry relationships, and synthesises a structured answer.

**Supported question types:**

| Question | Intent |
|----------|--------|
| "Have we seen this before?" | Historical lookup |
| "What do we know about X?" | Summary across all types |
| "Show related bugs / ADRs / tasks" | Type-filtered search |
| "What is currently blocked?" | Blocked task filter |
| "What changed recently?" | Recent activity by `updated_at` |
| "What should I read first about X?" | Onboarding by connectivity |

**Examples:**

```bash
monday ask "Have we seen Homebrew PATH issues before?"
monday ask "Summarize everything we know about Weather observations."
monday ask "Show all ADRs related to search."
monday ask "What is currently blocked?"
monday ask "What changed recently?"
monday ask "What should I read first to understand the task system?"
```

**Output:**
```
────────────────────────────────────────────────────────────
Yes — 1 entry/entries found related to 'homebrew path'.

Most relevant: [BUG-0001] Homebrew PATH Fix
Resolved by adding /opt/homebrew/bin to PATH in .zshrc.
────────────────────────────────────────────────────────────

Confidence : 20%
Engine     : monday-reasoning/1.0
Sources    : BUG-0001

Supporting entries:
  [BUG-0001] Homebrew PATH Fix  (bug)

Suggested next actions:
  1. monday.search('BUG-0001') — retrieve full entry: Homebrew PATH Fix
  2. monday.search('resolved homebrew path') — check for resolution
  3. monday.learn(content="...", entry_type="pattern") — add new knowledge about 'homebrew path'
```

**Confidence score:**
Ranges from 0% (no evidence found) to a maximum of 95% (never 100%, since no LLM validation is applied). When an LLM integration is added, the engine remains the same but confidence can reach 100%.

---

### `monday search`

Keyword search across the knowledge base.

```
monday search "<query>" [--limit N]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--limit N` | 10 | Maximum number of results to display. |

**Examples:**

```bash
monday search "rate limit"
monday search "homebrew" --limit 5
monday search "decision"
```

**Output:**
```
Results for "rate limit" (2 found)
────────────────────────────────────────────────────────────
  1. [PAT-0001] Rate limit pattern
     Type: pattern  Tags: api, rate-limit
     Use exponential backoff when hitting the OpenAI rate limit.

  2. [BUG-0002] Rate limit exceeded on load test
     Type: bug  Tags: openai, load-test
     Hit rate limit during stress test at 1000 RPM.

```

---

### `monday learn`

Add a new knowledge entry to MondayOS. Supports three input modes:

**Non-interactive (all flags):**
```bash
monday learn \
  --title "Homebrew PATH Fix" \
  --type bug \
  --tags "homebrew,macos" \
  --components "tooling" \
  --content "Resolved by adding /opt/homebrew/bin to PATH in .zshrc."
```

**Stdin pipe:**
```bash
cat fix.md | monday learn --title "Homebrew PATH Fix" --type bug
echo "Fix: add to PATH." | monday learn --title "Fix" --type bug --tags homebrew
```

**Interactive prompts** (run `monday learn` with no flags):
```
Title: Homebrew PATH Fix
Type [pattern]: bug
Tags (comma-separated, or Enter to skip): homebrew,macos
Components (comma-separated, or Enter to skip):
Content (Ctrl-D on a blank line when done):
Resolved by adding /opt/homebrew/bin to PATH in .zshrc.
^D

Stored as BUG-0001  (bug)
```

**Flags:**

| Flag | Default | Description |
|------|---------|-------------|
| `--title, -t TEXT` | `""` | Entry title. Extracted from content if empty. |
| `--type, -T TYPE` | `pattern` | Knowledge type (see table below). |
| `--tags, -g TAG,TAG` | — | Comma-separated tags. |
| `--components, -c COMP,COMP` | — | Comma-separated component names. |
| `--content, -C TEXT` | — | Body text. Reads from stdin if omitted. |

**Valid types:**

| Type | ID prefix | Use for |
|------|-----------|---------|
| `bug` | `BUG-` | Defects, errors, failures |
| `decision` | `DEC-` | ADRs, architectural choices |
| `task` | `TASK-` | Task-type knowledge entries |
| `sprint` | `SPR-` | Sprint summaries |
| `feature` | `FEA-` | Feature specifications |
| `lesson` | `LES-` | Retrospective lessons |
| `pattern` | `PAT-` | Reusable engineering patterns |
| `runbook` | `RUN-` | Operational procedures |
| `documentation` | `DOC-` | Reference documentation |
| `research` | `RES-` | Research findings |
| `weather` | `WEA-` | Weather observations |
| `experiment` | `EXP-` | Experimental results |

---

### `monday task`

Manage tasks through the public API.

#### `monday task list`

List all active (non-terminal) tasks.

```bash
monday task list [--status STATUS] [--priority PRIORITY] [--type TYPE]
```

| Flag | Description |
|------|-------------|
| `--status STATUS` | Filter by status: `backlog`, `assigned`, `in-progress`, `blocked`, `review` |
| `--priority PRIORITY` | Filter by priority: `P0`, `P1`, `P2`, `P3` |
| `--type TYPE` | Filter by type: `feature`, `fix`, `refactor`, `docs`, `research`, `ops`, `review` |

**Examples:**
```bash
monday task list
monday task list --status in-progress
monday task list --priority P0
monday task list --status blocked
```

**Output:**
```
Active tasks (2)
────────────────────────────────────────────────────────────
  TASK-0001  [P2] [backlog]      Wire up the task API
  TASK-0002  [P0] [in-progress]  Fix production auth bug
```

#### `monday task create`

Create a new task in `BACKLOG` status.

```bash
monday task create --title "..." --objective "..." [options]
```

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--title TEXT` | yes | — | Task title. |
| `--objective TEXT` | yes | — | What the task must achieve. |
| `--type TYPE` | no | `feature` | Task type. |
| `--priority PRIORITY` | no | `P2` | Priority level. |
| `--created-by WHO` | no | `human:cli` | Creator identifier. |
| `--project NAME` | no | — | Bind the task to one registered external project (or `mondayos`). |

**Example:**
```bash
monday task create \
  --title "Implement rate limit handling" \
  --objective "Apply exponential backoff on 429 responses." \
  --type fix \
  --priority P1
```

For external delivery, bind the task when it is created. The project must
already be registered:

```bash
monday task create \
  --title "Implement rate limit handling" \
  --objective "Apply exponential backoff on 429 responses." \
  --type fix \
  --priority P1 \
  --project customer-portal
```

**Output:**
```
Created TASK-0001
  Title    : Implement rate limit handling
  Status   : backlog
  Priority : P1
  Type     : fix
```

#### `monday task get`

Retrieve a task by ID.

```bash
monday task get TASK-0001
```

**Output:**
```
TASK-0001 — Implement rate limit handling
────────────────────────────────────────────────────────────
  Status    : backlog
  Priority  : P1
  Type      : fix
  Created   : 2026-06-27
  By        : human:cli

  Objective : Apply exponential backoff on 429 responses.
```

#### `monday task complete`

Mark a task as `COMPLETED`. The task must be in a state that allows completion (`in-progress` or `review`).

```bash
monday task complete TASK-0001 [--reason "..."] [--changed-by WHO]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--reason TEXT` | `""` | Completion notes, appended to status history. |
| `--changed-by WHO` | `human:cli` | Who is completing the task. |

**Valid pre-completion transitions:**  
`in-progress → completed` or `review → completed`

**Example:**
```bash
monday task complete TASK-0001 --reason "Merged in PR #42. Rate limits handled."
```

**Output:**
```
TASK-0001 marked COMPLETED
```

---

## `monday project`

Register and inspect external source repositories. Registration supports
analysis and onboarding by default; autonomous branch/PR delivery requires the
additional exact GitHub repository binding.

```bash
monday project register NAME /absolute/path [--description TEXT] \
  [--delivery-repository OWNER/REPO] [--overwrite]
monday project list
monday project get NAME
monday project remove NAME
```

`--delivery-repository OWNER/REPO` is the one-time opt-in that permits MondayOS
to push a delivery branch and open a pull request for that target. It is an
identity such as `acme/customer-portal`, not a URL, credential, branch, or local
path. MondayOS pins it together with the target's canonical local directory and
filesystem identity. Delivery fails if that binding later changes.

Registration without this flag remains analysis-only and cannot be selected by
`monday build run`. To upgrade an existing registration, repeat the exact
registration with `--delivery-repository` and `--overwrite`:

```bash
monday project register customer-portal /path/to/customer-portal \
  --description "Customer web application" \
  --delivery-repository acme/customer-portal \
  --overwrite
monday project get customer-portal
```

The source repository must be a standalone checkout with its own real `.git`
directory and must be separate from the MondayOS controller root. Git linked
worktrees and linked files inside `.git` are not accepted as delivery targets.
A branch push may start CI, preview environments, or other automation already
configured by the target repository. MondayOS does not merge or deploy.

---

## `monday build`

Build an existing task in an isolated worktree, run fixed validation, obtain a
fresh high-confidence ChatGPT/Codex review, and open a GitHub pull request. This
workflow never merges or deploys.

```bash
monday build run TASK-0001 [--project NAME] [--id DELIVERY-ID] [--json]
monday build get DELIVERY-ID [--project NAME] [--json]
monday build history [--task TASK-ID] [--project NAME] [--limit N] [--json]
monday build capabilities [--project NAME] [--json]
```

Omit `--project` (or pass `--project mondayos`) to select MondayOS itself. For external delivery, the task must
already be bound to the same registered, delivery-enabled project:

```bash
monday build capabilities --project customer-portal
monday build run TASK-0002 --project customer-portal
monday build history --project customer-portal
```

`build get --project` asserts that the saved delivery belongs to that project;
`build history --project` filters the controller's delivery records. No build
command accepts a raw target path or Git URL.

`capabilities` is read-only and reports the selected builder, ChatGPT/Codex
Reviewer, validation, repository, and GitHub readiness. MondayOS self-builds can
use Claude Code with Codex fallback; external projects use the controlled-context
Codex builder only. All builds require the separate high-confidence
ChatGPT/Codex review. `capabilities` exits with status `1` when the report says
`ready: false`, including with `--json`, so startup scripts can use it as a real
preflight check. The JSON response still has `success: true` when the inspection
itself completed; the runnable verdict is `data.ready`.

The workflow stops after opening the pull request. It never merges or deploys.
External validation always includes the staged-diff check and runs sandboxed
pytest only when the target contains `pyproject.toml`; it does not install target
dependencies or infer commands for other ecosystems. See
[BUILD_WORKFLOW.md](BUILD_WORKFLOW.md) for the complete trust model, validation
limits, and repair loop.

---

## `monday telegram`

Run the private Telegram control plane using the project-local `.env`:

```bash
monday telegram [--once] [--identify] [--provider NAME]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--once` | off | Ingest one long-poll response, drain the durable inbox synchronously, and exit. Useful during setup. |
| `--identify` | off | Print pending Telegram user/chat IDs and exit. |
| `--provider NAME` | role defaults | Pin one provider for productive team stages. The final Reviewer still requires OpenAI/ChatGPT (`fake` is available for offline tests). Without a pin, productive roles use their preferred provider with ordered fallback. |

`TELEGRAM_BOT_TOKEN` and `MONDAYOS_TELEGRAM_ALLOWED_USER_IDS` are required.
Set `MONDAYOS_TELEGRAM_LIVE_BUILD=true` to make private plain text, `/build`, and
`/deliver` use the autonomous PR workflow. It is off by default.
`/build PROJECT :: REQUEST` creates a project-bound request, and `/deliver` uses
the existing task's saved project binding automatically.
In normal daemon mode, accepted minimal requests are atomically saved in
`logs/telegram/state.json` before the Telegram offset advances. The bot returns
a saved receipt with the FIFO queue position and keeps polling while one
non-daemon worker exclusively runs Monday/model work. Restart recovers pending
jobs with stable identities, and graceful shutdown finishes the active job while
leaving the remainder queued.
See [TELEGRAM.md](TELEGRAM.md) for setup, commands, restart behavior, and the
always-on Mac mini service.

---

## Error handling

All errors print to `stderr` and exit with code `1`:

```
Error: Task not found: TASK-9999
Error: Cannot transition from 'backlog' to 'completed'
Error: Unknown entry_type 'nonsense'. Valid values: [...]
Error: no content provided.
```

Argument errors (missing required flags, invalid choices) print argparse usage to `stderr` and exit with code `2`.

Successful commands exit with code `0`. The deliberate exception is
`monday build capabilities`: a completed inspection exits with code `1` when
`data.ready` is false, as described above.

---

## Automation and scripting

The CLI is designed for use in scripts and automation:

```bash
# Learn from a file
cat docs/runbook.md | monday learn --title "Deploy runbook" --type runbook

# Check for blocks before a standup
monday ask "What is currently blocked?" > standup-notes.txt

# Search and process results
monday search "rate limit" --limit 5

# Create a task from CI
monday task create \
  --title "Investigate failing test" \
  --objective "Test test_foo has been failing on main for 3 runs." \
  --type fix \
  --priority P1 \
  --created-by "ci:github-actions"
```

The `--project-root` flag makes the CLI usable from any working directory:

```bash
monday --project-root ~/projects/myproject status
monday --project-root ~/projects/myproject task list
```

---

*Document owner: Lead Software Engineer  
Last updated: Sprint 1.5 (2026-06-27)*
