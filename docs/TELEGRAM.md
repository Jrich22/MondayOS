# MondayOS Telegram Control Plane

Telegram is MondayOS's first remote control surface for the Mac mini. It uses
outbound long polling, so it does not open a public port or require a tunnel.

## What this increment does

- Accepts private text as either an advisory-team request (default) or an
  autonomous live build (explicit opt-in).
- Creates a durable MondayOS task with Telegram provenance.
- Runs the MondayOS agent team in review mode by default.
- With live builds enabled, edits an isolated worktree, validates, automatically
  repairs, requires ChatGPT approval of the exact artifact, and opens a PR.
- Reports team/build progress back to the originating chat.
- Once a live-build request's acceptance receipt is confirmed, its starting,
  phase, and still-running messages are informational and best-effort. A
  Telegram outage cannot stop or dead-letter the accepted exact delivery;
  confirmed sends alone advance their low-noise deduplication markers.
- Automatically retries a live build when the delivery controller explicitly
  classifies a pre-publication model or tool failure as temporary. Delayed
  retries are request-free, survive restarts, and do not block newer requests.
- Stages terminal and retry-scheduled live-build messages in a bounded,
  request-free delivery outbox before removing the completed source request.
  A Telegram send outage therefore cannot keep finished work or its request
  text in the active FIFO.
- After a successful live build, durably watches the exact reviewed PR and sends
  restart-safe follow-ups only when its aggregate checks or lifecycle meaningfully
  change. Observation failures back off without exposing remote diagnostics.
- Supports task/system status, active-task listing, and explicit run commands.
- Supports idempotent approval and rejection of reviewed runs.
- Atomically saves each accepted, minimal request, its request-free acceptance
  receipt, and the advanced Telegram offset in `logs/telegram/state.json`.
  The worker cannot execute that request until Telegram receipt delivery is
  durably confirmed. Unauthorized or otherwise ignored content is never queued.
- Bounds the durable inbox at 25 pending jobs. Every accepted request receives a
  stable `JOB-<Telegram update ID>` handle. Receipt delivery retries with
  bounded backoff from local state, rechecks the current allowlists, and remains
  independent of Telegram update retention. A full inbox is refused durably,
  without retaining the rejected request text. Its bounded, request-free notice
  outbox advances the update offset immediately, so a send outage cannot block
  later `/queue` or `/cancel` controls.
- Keeps private, allowlisted `/queue` and queued-only `/cancel JOB-ID` controls
  responsive on the polling path while model/build work is busy. These controls
  expose only job handles, phases, and positions—not saved request text.
- Keeps polling and acknowledging new requests while one non-daemon worker
  exclusively runs Monday/model work. Commands execute in FIFO order, and
  provider/build concurrency stays at one because a MondayOS instance and its
  providers are intentionally not thread-safe.
- Recovers queued, running, waiting, and retrying work after restart with the
  same Telegram job, task, run, and delivery identities. It retries an
  unexpected poison failure at most three times, then terminalizes that job so
  later work can proceed.
- Revalidates each persisted request's update identity, digest, message shape,
  origin, and resumable phase immediately before the worker can execute it.
- Removes active request text when a job becomes a terminal replay tombstone.
  A graceful stop finishes the current job and leaves the rest durably queued.

Telegram is the transport until an accepted request and its receipt outbox are
saved. After that atomic checkpoint, the local queue—not Telegram's temporary
update retention—is the source of truth for pending work. Receipt sends are
at-least-once: a crash after Telegram accepts the message but before the local
checkpoint can produce the same fixed receipt twice, while the job still runs
only once.

After that receipt is durably confirmed, live-build starting, phase-progress,
and nonterminal reconciliation messages never govern execution. They may be
skipped during a Telegram outage or repeated after an uncheckpointed remote
send, but the task and deterministic delivery identity continue locally.
Terminal build results, scheduled-retry notices, and PR lifecycle changes are
not best-effort: they retain their bounded request-free outboxes and independent
retry policy.

## Create the bot

1. In Telegram, open the verified **@BotFather** account.
2. Send `/newbot` and follow its instructions.
3. Save the bot token locally. Never paste it into source code or GitHub.
4. Put only the token into the local `.env`, send `/start` to the new bot, and run:

   ```bash
   monday telegram --identify
   ```

   MondayOS prints the pending message's numeric user and chat IDs locally. Add
   your user ID to the allowlist below; no third-party ID bot is required.

Create a project-local `.env` file (already excluded from Git):

```text
TELEGRAM_BOT_TOKEN=replace-with-botfather-token
MONDAYOS_TELEGRAM_ALLOWED_USER_IDS=replace-with-your-numeric-user-id

# Optional: comma-separated numeric group chat IDs. Groups expose only
# /build, /run, /status, and /help; approvals remain private-chat-only.
MONDAYOS_TELEGRAM_ALLOWED_CHAT_IDS=-1001234567890

# Optional: pin the productive team stages to one provider.
# The final reviewer still requires OpenAI/ChatGPT. Leave unset for role-aware fallback.
MONDAYOS_TELEGRAM_PROVIDER=deepseek

# Optional: private-chat autonomous builds. Default is false.
# Requires Codex/ChatGPT and GitHub CLI authentication; Claude Code is optional.
MONDAYOS_TELEGRAM_LIVE_BUILD=true
```

Provider credentials remain in this same local environment file, as documented
in [PROVIDERS.md](PROVIDERS.md). MondayOS never intentionally copies the Telegram
token or AI keys from configuration into task files, run logs, status output, or
GitHub; keep secrets out of request text and other user-controlled content too.

## Run it

From the MondayOS project root:

```bash
monday telegram
```

To ingest one long-poll response, drain the durable inbox synchronously, and
then exit:

```bash
monday telegram --once
```

`--once` is a setup and queue-drain mode, not a daemon. It does not run the
background delivery-retry, pull-request follow-up, or lifecycle-notice loops and
therefore does not provide ongoing recovery or monitoring. Saved retries,
open-PR follow-ups, and lifecycle notices remain durable and resume the next
time the normal `monday telegram` service starts, where GitHub is observed
before a saved lifecycle notice is sent.

To discover the numeric ID of someone who has messaged the bot:

```bash
monday telegram --identify
```

Identification takes the same single-worker lock as the daemon and never confirms
the pending messages it reads. It refuses to run while the normal worker is live,
so setup cannot steal or drop a real request.

To check local daemon readiness without contacting Telegram or exposing the bot
token:

```bash
monday telegram --health
monday telegram --health --json
```

The JSON form is suitable for monitoring and exits nonzero when configuration,
durable state, the worker lock, supervisor heartbeat, either runtime lane, or
required live-build capabilities are unhealthy, when the inbox is not accepting
new work, or while an acceptance receipt, capacity refusal, or live-build
delivery notice remains unresolved. Intake and worker are checked independently:
a fresh supervisor cannot hide a stuck poll, dispatch, team, build, validation,
review, push, or pull-request operation. Idle workers remain ready; startup,
backoff, stopped, expired, malformed, future-dated, and legacy heartbeat records
fail closed. Restart the normal daemon to replace a legacy heartbeat. Full
installation and recovery procedures are in
[SETUP_GUIDE.md](SETUP_GUIDE.md) and [RUNBOOK.md](RUNBOOK.md).

`monday telegram --watchdog --json` is a separate liveness-only probe for the
fixed `com.mondayos.telegram` launchd service. It requires two matching samples
for the exact heartbeat `started_at` generation and unchanged progress evidence
45–150 seconds apart. New progress or a longer gap resets the evidence window.
It rechecks before and after checkpointing a restart and verifies recovery only
by a new `started_at`. Its restart budget is one request per five minutes and
at most three per hour. Unlike `--health`, it ignores queue pressure, acceptance,
refusal, delivery, and lifecycle outboxes, provider availability, and
live-build readiness because restarting cannot repair them. The daemon service
must be loaded, its worker lock held, and its heartbeat valid with an exact
`started_at`; a missing lock, missing or invalid heartbeat, or absent generation
fails closed with a non-restartable `state-unreadable` result for operator
diagnosis.

At startup MondayOS verifies the bot identity and removes any prior webhook
without dropping queued updates. Polling and webhooks cannot be used at the same
time, so this ensures one predictable delivery path.

The saved offset, durable FIFO jobs, delivery-retry checkpoints, and replay
tombstones in `logs/telegram/state.json` are bound to the bot's numeric Telegram
identity.
Renaming the same bot keeps its state. Replacing the token with a different bot
clears the old bot's update state before polling, so one bot's offset cannot
silently discard another bot's pending messages.

## Commands

With live builds disabled (the default):

```text
normal text             create and run a task
/build REQUEST          create and run a task
/build PROJECT :: REQUEST
                        create a task explicitly bound to a registered project
/run TASK-ID            run an existing task
/status [TASK-ID]       system or task status
/tasks                  list active tasks
/queue                  show your pending jobs without request text
/cancel JOB-ID          cancel one job that has not started
/approve RUN-ID         approve a reviewed run
/reject RUN-ID REASON   reject a reviewed run
/help                   show help
```

With `MONDAYOS_TELEGRAM_LIVE_BUILD=true` in a private chat:

```text
normal text             create, build, validate, review, and open a PR
/build REQUEST          create, build, validate, review, and open a PR
/build PROJECT :: REQUEST
                        do the same in that registered project's repository
/deliver TASK-ID        deliver an existing task through a PR
/run TASK-ID            run the advisory agent team (unchanged)
```

`PROJECT` is a registered MondayOS project name, never a filesystem path or Git
URL. Live delivery additionally requires that project to have been explicitly
enrolled with its exact GitHub repository identity. Spaces around `::` are
optional; when the delimiter is present, both the project name and request are
required. `/deliver` reads the saved project binding from the task, so a
project-bound task is delivered to that registered repository automatically;
unbound and `mondayos` tasks continue to target MondayOS itself. The Telegram
checkpoint also saves the selected project, and a retry cannot rebind the same
update to another target.

Only allowlisted Telegram users are processed. Unauthorized messages are ignored
without revealing whether the bot controls MondayOS. Group messages are ignored
unless the chat is separately allowlisted, and group chatter never becomes a
task implicitly. In an allowlisted group, a command addressed with
`@bot_username` runs only when that username matches this verified bot; commands
addressed to another bot are ignored.

Allowlisted groups intentionally expose only `/build`, `/run`, `/status`, and
`/help`; `/queue` and `/cancel` are private-chat-only. A group `/build` always
uses the advisory team even when private live builds are enabled; `/deliver` is
ignored. Task listings and approval decisions
(`/tasks`, `/approve`, `/reject`) remain private-chat-only even when the group
itself is allowlisted. A user can open the bot's private chat to list or cancel
their own still-queued requests that they originally submitted in an allowed
group; another user's group requests remain hidden.

## Pull-request lifecycle follow-ups

When a private live build reaches `pr-open`, MondayOS checkpoints a request-free
follow-up record before acknowledging the Telegram job. The saved identity fixes
the delivery, task, project, originating chat, and PR URL. Each poll then goes
through the public delivery observer, which also revalidates the saved GitHub
repository, base branch, head branch, PR number and URL, and exact reviewed commit
SHA. The observer is read-only at GitHub: it cannot push, edit, close, merge, or
deploy the pull request.

Telegram receives only the PR lifecycle and one aggregate check state:
`pending`, `passed`, `failed`, `no-checks`, `timed-out`, or `unknown`. It never
sends or persists individual check names, descriptions, annotations, logs, or
raw observation errors. An open `unknown` state is checkpointed silently.
Repeated observations are silent; the bot sends a message only for a meaningful
reportable transition, such as checks becoming pending, passed, failed, absent,
or timed out, or the PR becoming merged, closed, or divergent from the reviewed
SHA.

The follow-up phase, next poll time, and last observation are stored in the
completed job tombstone in `logs/telegram/state.json`, without retaining the
original request text. Each accepted observation atomically advances or
completes that watcher and stages at most one source-bound semantic message in
a separate lifecycle-notice outbox. A newer transition supersedes an unsent
older one, so a terminal result cannot sit behind a stale open-check message.
A restart can reclaim a poll that was in flight or deliver an already-recorded
notice without observing GitHub again.

GitHub and Telegram use independent clocks. Pending checks are polled more
frequently, settled open-PR states more slowly, and transient observation
failures use exponential backoff capped at five minutes. Telegram rate limits
create a durable lane-wide pause for current and newly created lifecycle notices
for up to 24 hours; they never defer GitHub observation or task reconciliation.
Untrusted `retry_after` values are
normalized before they reach logs or time arithmetic; invalid values use a safe
default and oversized values are capped at 24 hours. A revoked allowlist,
blocked bot, deleted chat, or other permanent destination failure terminalizes
and scrubs only the notice. The watcher continues until a verified terminal PR
state or its durable 30-day lifetime. At most 500 watchers and 500 unresolved
lifecycle notices can be active. Capacity exhaustion fails closed without
changing the pull request or retaining request text.
When that 30-day watcher lifetime expires, any unsent open-state notice is
resolved and scrubbed atomically so a later restart cannot send stale monitoring
status or leave health wedged behind an expired watcher.

The same separation applies when automatic delivery recovery opens the pull
request: a delayed or permanently undeliverable retry-outcome message cannot
pause or terminate the new PR watcher.

Lifecycle-message delivery is at-least-once. A crash after Telegram accepts a
fixed message but before MondayOS checkpoints it may repeat that same message.
It cannot repeat GitHub observation merely to recover delivery, recreate the
build, or apply a second non-idempotent task mutation.

Open-PR check results do not drive MondayOS task state. An observation may
repair the delivery's separately recorded, explicitly `review-pending` initial
transition, but only for the same reviewed PR and the unchanged complete task
revision. Terminal reconciliation remains separate: an exact reviewed merge
completes an eligible task, while a closed or SHA-diverged PR returns a task
still in `REVIEW` to `IN_PROGRESS` unless a newer open delivery remains
authoritative. A newer open delivery also prevents an older merged PR from
completing the task underneath newer work. Human-managed states are not
overridden. The bot reports that result but still never merges or deploys it.

## Bounded inbox and cancellation

MondayOS accepts at most 25 pending Telegram jobs across the durable inbox.
Admission atomically creates a request-free receipt record containing only the
stable request digest and minimal destination identity. Receipt text is derived
from the stable `JOB-ID`, never from the request. Transient Telegram failures
back off durably across restart without blocking `/queue` or `/cancel`, and an
older unresolved receipt prevents newer work from bypassing FIFO. Current user
and group allowlists are checked on every attempt. Revocation or a permanent
destination failure scrubs the unstarted request and releases capacity rather
than executing work the user cannot identify or cancel. On schema migration,
pristine version 5 queued jobs receive pending receipts; already-started or
checkpointed work remains eligible so recovery is never discarded.

When it is full, the original update becomes a request-free refusal tombstone.
The same atomic write advances its Telegram offset and creates a minimal notice
outbox, so refusal delivery never gates later polling. A transient failure or
rate limit records bounded backoff and survives restart; `/queue`, `/cancel`,
and other updates continue through the control plane while the notice waits.
Recovery does not rely on Telegram redelivering the original update and never
re-admits or retains the rejected request. A retry also rechecks the current
user and group allowlists and verifies that the minimal saved route is still
bound to the original update and request digest; a revoked, malformed, or
redirected destination is not contacted. Because pre-v7 capacity notices have
no trustworthy route binding, migration terminalizes and scrubs them while
advancing their offset. The user is told that the request was not saved and can
use `/queue` or `/cancel` before trying again. At most 500 unresolved refusal
notices are retained; after that bound, newer refusals are safely terminalized
without retaining their route.
Existing state with more than 25 pending jobs remains loadable and drains
normally; the cap applies to new admission. `monday telegram --health` reports
the current usage, unresolved acceptance receipts, and unresolved refusal
notices, plus unresolved live-build delivery and PR lifecycle notices. It is
unhealthy at or above the cap or while any of those outboxes remains unresolved.

`/queue` lists only jobs owned by the same allowlisted user: requests from that
private chat plus requests that user submitted in an allowed group. `/cancel
JOB-ID` uses the same ownership rule and intentionally gives no indication that
another user's handle exists. Cancellation is allowed only while a job is still
queued and has no side-effect checkpoint, including after its acceptance
receipt is sent but before work is claimed. Running, waiting, retrying, or
otherwise started work is not interrupted.
Cancellation is durable, survives restart, releases inbox capacity, and removes
the saved request text. A replay receives the same safe confirmation without
executing the job twice.

## Automatic delivery recovery

Terminal live-build results and retry-scheduled messages cross a separate
durability boundary from their source jobs. Before the worker acknowledges a
completed source job, it stages the safe user-facing message in a bounded,
request-free delivery-notice outbox. The normal acknowledgment can then scrub
the original request and release the FIFO even when Telegram is unavailable.
The outbox retries independently after restart; it never reruns the build or
creates a second retry checkpoint merely to recover a notification.

Each notice retains only its fixed kind and safe structured delivery fields,
minimal private destination, bounded retry metadata, and a deterministic
binding to the source update, request digest, origin, task, and delivery. It
does not retain the request body, provider diagnostics, credentials, or other
free-form failure text. Before every send, MondayOS verifies that binding and
rechecks the current private destination and allowlists. Malformed, redirected,
revoked, or permanently failed notices are terminalized and scrubbed instead of
contacting a different destination. A crash at Telegram's remote/local
checkpoint boundary can duplicate the same fixed message, but cannot duplicate
the build or retry scheduling.

A live delivery can fail before commit or push because a model session, network
connection, or required tool is temporarily unavailable. It can also be left
incomplete when its worker exits during implementation, validation, review,
commit, or the final pre-publication checkpoint. MondayOS retries only when the
delivery controller provides the structured `transient` failure class. For an
interruption, the controller first revalidates the owned worktree and proves
that its unique branch is absent from the pinned remote. The interrupted record
preserves the original source phase and a SHA-256 digest that binds that phase to
the delivery lineage, task and repository target, owned worktree, base, branch,
and any local commit. Changed or incomplete proof fields cannot authorize a
retry. Words such as "temporary" or "rate limit" in model, CLI, or remote
diagnostic text never authorize a retry. Policy failures, validation/repair
exhaustion, reviewer verdicts, missing setup, unclassified failures, and
interruptions without that remote-absence proof remain terminal.

Before invoking the original live build, MondayOS saves its fixed 24-hour
recovery deadline. Before acknowledging a retryable terminal Telegram job, it
saves a request-free
retry checkpoint containing the task, registered project, originating chat, and
a deterministic next delivery identity. The original request text is then
removed in the normal replay tombstone. A delayed retry runs outside the main
FIFO, so new Telegram requests continue while it waits. Restarts reclaim the
same checkpoint. If a crash lands after the safe transient result is saved but
before that checkpoint is created, resume reconstructs the same deterministic
next identity within the original saved 24-hour deadline, without rerunning the
root delivery. Downtime never resets that deadline. A crash during a retry
reconciles that exact identity instead of opening a duplicate pull request.

Each retry is a new immutable delivery record linked by deterministic identity.
The scheduler passes the exact controller-owned immediate predecessor, root
delivery, and retry attempt. The controller validates the resulting `ROOT-rN`
identity, derives the sole permissible predecessor from durable history, and
rejects omitted, substituted, truncated, or rebound lineage. It re-proves the
complete bounded chain before successor reservation, under the task lock, and
after each publication progress callback immediately adjacent to push and
pull-request creation. An older branch that appears during the delay or build,
any other matching live pull request regardless of its timestamps, a changed
target, or unavailable proof stops the successor before the next irreversible
operation.
MondayOS makes at most six delivery runs over a bounded 24-hour window, using
increasing delays and any longer controller-provided retry hint. It never starts
a fresh identity after a remote branch, push, pull request, or uncertain
publication; those states must reconcile the original delivery. A local commit
from a crashed worker is eligible only after the controller proves the unique
remote branch is absent, and the successor does not reuse that commit. Telegram
receives only generic scheduled, recovered, or exhausted notices—raw provider
diagnostics and credentials are neither copied into retry state nor sent to the
chat.

The 24-hour cutoff prevents an unstarted retry from beginning late. If an exact
retry identity was already invoked before the cutoff, MondayOS still reconciles
that same identity after restart so a commit or pull request cannot be lost or
misreported as an expiration. Post-cutoff reconciliation is bounded: if the
exact identity still cannot be verified, MondayOS records a neutral
`publication-pending` outcome, stops automatic retries, releases scheduler
capacity, and asks the operator to inspect that delivery in MondayOS or GitHub.

## Always-on Mac mini service

An example launchd definition lives at
`deploy/launchd/com.mondayos.telegram.plist.example`. Replace all five occurrences
of its root-path placeholder, copy it to
`~/Library/LaunchAgents/com.mondayos.telegram.plist`,
and load it with launchd. It contains no credentials; `monday telegram` reads the
Git-ignored project `.env` at startup.

The example defines an explicit, minimal `PATH` because launchd does not inherit
your interactive shell setup. Delivery does not trust that ambient `PATH` when
choosing authenticated tools: it resolves Codex from the ChatGPT app bundle or
standard Homebrew/system locations, and Claude Code and GitHub CLI from standard
Homebrew/system locations. Install those tools in one of those supported
locations; adding an arbitrary directory to the plist does not enroll it as a
trusted executable. Never put tokens or API keys in the plist.

```bash
chmod 600 .env
mkdir -p logs ~/Library/LaunchAgents
cp deploy/launchd/com.mondayos.telegram.plist.example \
  ~/Library/LaunchAgents/com.mondayos.telegram.plist
# Replace every REPLACE_WITH_MONDAYOS_ROOT in the copied file with this repo's
# absolute path, then validate and start it:
plutil -lint ~/Library/LaunchAgents/com.mondayos.telegram.plist
launchctl bootstrap "gui/$(id -u)" \
  ~/Library/LaunchAgents/com.mondayos.telegram.plist
launchctl print "gui/$(id -u)/com.mondayos.telegram"
```

For bounded liveness recovery, install the second credential-free LaunchAgent:

```bash
cp deploy/launchd/com.mondayos.telegram-watchdog.plist.example \
  ~/Library/LaunchAgents/com.mondayos.telegram-watchdog.plist
# Replace all five REPLACE_WITH_MONDAYOS_ROOT occurrences with the same root.
plutil -lint ~/Library/LaunchAgents/com.mondayos.telegram-watchdog.plist
launchctl bootstrap "gui/$(id -u)" \
  ~/Library/LaunchAgents/com.mondayos.telegram-watchdog.plist
launchctl print "gui/$(id -u)/com.mondayos.telegram-watchdog"
```

It runs `monday --project-root ROOT telegram --watchdog --json` at load and
every 60 seconds. The watchdog plist has an explicit `PATH` but no credentials
and no `KeepAlive`; its output paths are
`logs/telegram-watchdog-stdout.log` and
`logs/telegram-watchdog-stderr.log`.

Restart or stop the normal daemon with:

```bash
launchctl kickstart -k "gui/$(id -u)/com.mondayos.telegram"
launchctl bootout "gui/$(id -u)" \
  ~/Library/LaunchAgents/com.mondayos.telegram.plist
```

Diagnostics are written to `logs/telegram-stdout.log` and
`logs/telegram-stderr.log`. A startup failure is usually an invalid bot token,
an empty user allowlist, an unreplaced plist path, a required CLI installed
outside the supported trusted locations described above, or another worker
already holding `logs/telegram/bot.lock`.

## Current boundary

The default Telegram behavior still runs the advisory team and requires its
existing human approval. The opt-in private live-build path performs real code
delivery through `Monday.build()`: isolated patch application, fixed tests,
bounded repair, exact-artifact ChatGPT review, commit, push, and pull request.
It can target MondayOS itself or an explicitly enrolled registered project. It
does **not** accept an ad hoc path or Git URL, merge, deploy, or transcribe
voice. A delivery branch push may start automation already configured in the
selected GitHub repository.

Accepted work runs through the durable FIFO queue. The polling intake stays
responsive while its single worker performs a team run or live build, so model
and build concurrency remains one. Restart preserves stable work identities;
graceful shutdown finishes the active item and leaves later items queued. The
`--once` setup mode intentionally ingests and drains synchronously instead.
It does not execute the ongoing PR follow-up loop; use the normal daemon for
continuous lifecycle monitoring.

Queue state is local operational data under `logs/telegram/` and remains
Git-ignored. Terminal replay tombstones retain the identity needed for
deduplication but not the active request text. Full build details are in
[BUILD_WORKFLOW.md](BUILD_WORKFLOW.md).
