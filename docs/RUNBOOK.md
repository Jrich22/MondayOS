# MondayOS Telegram Operations Runbook

This runbook covers the always-on Mac mini service after
[SETUP_GUIDE.md](SETUP_GUIDE.md) is complete. Run commands from the MondayOS
repository root unless a command says otherwise.

## Service controls

Show service state:

```bash
launchctl print "gui/$(id -u)/com.mondayos.telegram"
```

Restart after changing `.env`, updating MondayOS, or refreshing an authenticated
CLI login:

```bash
launchctl kickstart -k "gui/$(id -u)/com.mondayos.telegram"
```

Stop the service:

```bash
launchctl bootout "gui/$(id -u)" \
  ~/Library/LaunchAgents/com.mondayos.telegram.plist
```

Start a stopped service:

```bash
launchctl bootstrap "gui/$(id -u)" \
  ~/Library/LaunchAgents/com.mondayos.telegram.plist
```

A graceful signal asks the active job to finish and leaves later jobs in the
durable queue. A system shutdown or service-manager timeout can still interrupt
that work; startup recovery reclaims its durable checkpoint. Do not start a
second interactive `monday telegram` process while the LaunchAgent is running;
the single-process lock intentionally rejects it.

## Routine health check

Run this at the start of the day and after every restart or update:

```bash
launchctl print "gui/$(id -u)/com.mondayos.telegram"
monday telegram --health
monday status
monday build capabilities
gh auth status
df -h .
tail -n 50 logs/telegram-stderr.log
```

Then send `/status` in the allowlisted private Telegram chat. Healthy means:

- launchd reports the service instead of `service not found`;
- `monday telegram --health` reports a held worker lock, fresh supervisor
  heartbeat, and ready `Intake` and `Worker` lanes;
- the inbox line reports fewer than 25 pending jobs and `accepting work`;
- the receipts line reports `0 pending`;
- the refusals line reports `0 pending`;
- the deliveries line reports `0 pending`;
- MondayOS status is healthy;
- build capabilities reports `ready: True` for subscription-based Codex review;
- GitHub CLI authentication is valid;
- the disk has comfortable free space; and
- Telegram responds without repeating startup or authentication errors.

For external monitoring or operator automation, use:

```bash
monday telegram --health --json
```

The command never contacts Telegram, never prints the bot token, and exits
nonzero when the local configuration, worker lock, supervisor heartbeat,
independent intake/worker progress leases, state, inbox capacity, unresolved
acceptance receipts, refusal notices, live-build delivery notices, or required
live-build capabilities are not ready. A lane in `starting`, `backoff`, or
`stopped`, or an active lane past its deadline, is deliberately unhealthy even
when the supervisor still pulses. An idle worker is healthy. If the heartbeat
is reported as legacy or invalid after an upgrade, restart the normal daemon;
never hand-edit `logs/telegram/heartbeat.json`.

## Bounded liveness watchdog

The separately loaded `com.mondayos.telegram-watchdog` LaunchAgent runs this
local, credential-free sample at load and every 60 seconds:

```bash
monday telegram --watchdog --json
```

This is deliberately narrower than `--health`. It may restart only when the
daemon service is loaded, its worker lock is held, a valid heartbeat supplies
an exact `started_at`, and that heartbeat reports a restartable stopped-daemon,
stale-supervisor, or stuck progress-lane failure.
A missing lock, missing or invalid heartbeat, or absent `started_at` is a
fail-closed `state-unreadable` observation for operator diagnosis, not a restart
trigger. The watchdog also does **not** restart for a full inbox, pending
acceptance, refusal, delivery, or lifecycle outboxes, provider availability,
or live-build readiness; those conditions require normal operator diagnosis
rather than a process restart.

Before restarting, the watchdog requires the same failure, exact daemon
generation (the heartbeat `started_at`), and unchanged relevant progress
evidence in two samples 45–150 seconds apart. New progress, a longer gap, or a
backwards clock adjustment starts a new evidence window. It rechecks that
fingerprint before and after checkpointing the restart request, then calls the
fixed target
`launchctl kickstart -k "gui/$(id -u)/com.mondayos.telegram"` and reports it as
verified only after a later sample observes a new `started_at`. A restart
request starts a five-minute cooldown, and no more than three requests are
allowed in any hour.

Inspect the service and its fixed output paths with:

```bash
launchctl print "gui/$(id -u)/com.mondayos.telegram-watchdog"
tail -n 50 logs/telegram-watchdog-stdout.log
tail -n 50 logs/telegram-watchdog-stderr.log
```

The durable debounce and restart checkpoint is
`logs/telegram/watchdog.json`. Do not hand-edit or delete it to bypass a
cooldown or exhausted hourly budget. If the watchdog reports
`service-not-loaded`, load the normal daemon; it intentionally does not
bootstrap a missing service.

Use `monday build history --limit 20` to review recent deliveries. For a known
delivery ID:

```bash
monday build get DELIVERY-ID
monday build observe DELIVERY-ID
```

`observe` is read-only at GitHub. It verifies the exact reviewed commit and
reports the aggregate pull-request/check state; it never pushes, edits, closes,
merges, or deploys. If that delivery explicitly records a pending initial
task-to-`REVIEW` transition, observation may safely repair only that local
transition after revalidating the unchanged complete task revision.

## Normal Telegram behavior

With live builds enabled in a private chat:

```text
normal text                 create, build, review, and open a PR
/build REQUEST              create, build, review, and open a PR
/build PROJECT :: REQUEST   target an enrolled registered project
/deliver TASK-ID            deliver an existing bound task
/run TASK-ID                run the advisory team
/status [TASK-ID]           show system or task status
/tasks                      list active tasks
/queue                      show your pending job handles and positions
/cancel JOB-ID              cancel a job that has not started
/help                       show supported commands
```

The durable FIFO acknowledges a request only after saving it. One worker runs
model/build work at a time while polling remains responsive. Delayed retries and
open-PR follow-ups survive restart. Do not resend a request merely because a
build is slow; use `/status TASK-ID` or the delivery history first.

The inbox admits at most 25 pending jobs. If Telegram reports that the inbox is
full, use `/queue` to inspect request-free job handles and `/cancel JOB-ID` to
remove an unstarted job, then submit the refused request again. A cancellation
cannot interrupt running, waiting, retrying, or side-effectful work. Do not edit
`logs/telegram/state.json` manually to make room; cancellation and normal job
completion release capacity safely and preserve replay protection. For a job
submitted in an allowed group, open Monday's private chat: the same submitting
user can see and cancel it there, while other users cannot.

Capacity-refusal notices retry from a bounded local outbox. A Telegram send
outage or rate limit may delay that notice, but it does not stop polling; a
later `/cancel` can still arrive and free capacity. Health remains nonzero while
any refusal notice is unresolved.

Accepted-job receipts, terminal live-build messages, and PR lifecycle messages
use separate bounded outboxes. A failed final or lifecycle send does not retain
the original build request, rerun the build, delay GitHub observation, or hold
later FIFO work. Leave the service running so it can retry. A blocked or revoked
destination can discard its message, but the exact PR watcher still reconciles
verified merge, closure, or divergence state. Health remains nonzero while a
retryable request-free message is unresolved.

Starting, phase-progress, and still-running live-build messages after the
accepted-job receipt are best-effort rather than outboxed. If one is absent,
do not resend the build request: Monday continues the same deterministic
delivery locally, and `/status TASK-ID` or delivery history remains the source
for diagnosis. A later phase can appear without an earlier courtesy message;
the terminal result and PR lifecycle notifications still use their durable
outboxes.

## Logs and safe rotation

Current daemon and watchdog output is in:

- `logs/telegram-stdout.log`
- `logs/telegram-stderr.log`
- `logs/telegram-watchdog-stdout.log`
- `logs/telegram-watchdog-stderr.log`

Queue state and delivery records are in `logs/telegram/` and `logs/delivery/`.
Those directories are operational state, not disposable logs.

Rotate only these four top-level output logs. Stop the watchdog first and then
the daemon so neither process keeps writing through an old file descriptor:

```bash
launchctl bootout "gui/$(id -u)" \
  ~/Library/LaunchAgents/com.mondayos.telegram-watchdog.plist
launchctl bootout "gui/$(id -u)" \
  ~/Library/LaunchAgents/com.mondayos.telegram.plist
mv logs/telegram-stdout.log "logs/telegram-stdout.$(date +%Y%m%d-%H%M%S).log"
mv logs/telegram-stderr.log "logs/telegram-stderr.$(date +%Y%m%d-%H%M%S).log"
mv logs/telegram-watchdog-stdout.log \
  "logs/telegram-watchdog-stdout.$(date +%Y%m%d-%H%M%S).log"
mv logs/telegram-watchdog-stderr.log \
  "logs/telegram-watchdog-stderr.$(date +%Y%m%d-%H%M%S).log"
touch logs/telegram-stdout.log logs/telegram-stderr.log \
  logs/telegram-watchdog-stdout.log logs/telegram-watchdog-stderr.log
launchctl bootstrap "gui/$(id -u)" \
  ~/Library/LaunchAgents/com.mondayos.telegram.plist
launchctl bootstrap "gui/$(id -u)" \
  ~/Library/LaunchAgents/com.mondayos.telegram-watchdog.plist
```

If any current log does not exist, skip its `mv`; launchd creates it on the
next start. Compress reviewed historical output logs and retain them according
to the operator's incident policy. Never rotate, truncate, or remove
`logs/telegram/state.json`, `logs/delivery/`, or an active delivery worktree.

List older output logs before deciding whether to archive them:

```bash
find logs -maxdepth 1 -type f -name 'telegram-*.log' -mtime +14 -print
```

## Backup

GitHub preserves source, commits, reviewed artifacts, and pull requests. It does
not preserve pending Telegram jobs, retries, local delivery records/worktrees,
agent runs, local project registration, or AI Workspace conversations.

For a consistent runtime backup, first stop the service and wait for the command
to return. Store the archive on an encrypted disk outside the repository:

```bash
launchctl bootout "gui/$(id -u)" \
  ~/Library/LaunchAgents/com.mondayos.telegram.plist
mkdir -p /Volumes/EncryptedBackup/MondayOS
MONDAYOS_BACKUP_FILE="/Volumes/EncryptedBackup/MondayOS/runtime-$(date +%Y%m%d-%H%M%S).tar.gz"
for MONDAYOS_RUNTIME_PATH in \
  tasks/active tasks/completed tasks/.sequences.json \
  tasks/ACTIVE.md tasks/DONE.md tasks/BACKLOG.md \
  agents/active agents/.sequences.json \
  config/projects.json workspace/conversations knowledge/runtime \
  logs/telegram logs/delivery logs/agents logs/executions \
  logs/publish logs/growth; do
  if [ -e "$MONDAYOS_RUNTIME_PATH" ]; then
    printf '%s\n' "$MONDAYOS_RUNTIME_PATH"
  fi
done | tar -czf "$MONDAYOS_BACKUP_FILE" -T -
chmod 600 "$MONDAYOS_BACKUP_FILE"
launchctl bootstrap "gui/$(id -u)" \
  ~/Library/LaunchAgents/com.mondayos.telegram.plist
```

Replace `/Volumes/EncryptedBackup` with the actual encrypted backup volume.
The archive intentionally excludes `.env`. Save `.env` separately in an
encrypted password manager or encrypted system backup and preserve its mode as
`600`. Never place either backup in Git or a public cloud folder without
encryption.

Back up before code upgrades, configuration changes, or manual recovery. Keep
at least one known-good backup from before the change and periodically test a
restore to a separate private directory.

## Restore and recovery

Restore only into the same repository path. Delivery records and registered
projects contain machine-specific absolute paths.

1. Stop the service.
2. Make a safety copy of the current runtime state; do not overwrite the only
   potentially recoverable copy.
3. From the repository root, extract the selected archive:

   ```bash
   tar -xzf /Volumes/EncryptedBackup/MondayOS/runtime-YYYYMMDD-HHMMSS.tar.gz
   ```

4. Restore `.env` from encrypted secret storage and enforce permissions:

   ```bash
   chmod 600 .env
   ```

5. Verify before starting:

   ```bash
   monday status
   monday build capabilities
   gh auth status
   ```

6. Start the LaunchAgent and send `/status`.

The normal daemon reclaims saved queued, running, waiting, and retrying work.
Do not use `monday telegram --once` as recovery for delayed retries or PR
follow-ups; it intentionally does not run those background loops. Do not delete
`bot.lock` to fix a lock error until `launchctl print` and process inspection
confirm that no Telegram worker is running.

## Updating MondayOS

Use a clean working tree and a runtime backup:

```bash
git status --short
git fetch origin
git pull --ff-only
source .venv/bin/activate
python -m pip install -e ".[dev]"
pytest
monday status
monday build capabilities
```

Restart the service after the checks pass. If the update adds or changes the
launchd template, compare it with the installed plist, preserve the absolute
root replacement, and never copy credentials into the plist.

## Rollback

For a bad source change, preserve history with a Git revert rather than deleting
files or resetting the repository:

```bash
launchctl bootout "gui/$(id -u)" \
  ~/Library/LaunchAgents/com.mondayos.telegram.plist
git status --short
git log --oneline -n 10
git revert BAD-COMMIT-SHA
source .venv/bin/activate
python -m pip install -e ".[dev]"
pytest
monday status
monday build capabilities
launchctl bootstrap "gui/$(id -u)" \
  ~/Library/LaunchAgents/com.mondayos.telegram.plist
```

Do not run the revert with uncommitted work. If the update was a merge commit,
have a maintainer choose the correct parent before reverting it. If runtime
schema/state—not source—is the problem, restore the last consistent runtime
backup instead of editing JSON by hand.

For a configuration rollback, restore the last known-good `.env` from encrypted
secret storage, run `chmod 600 .env`, and restart. For a plist rollback, restore
the known-good credential-free plist, run `plutil -lint`, then bootstrap it.

## Incident handling

### Bot is silent

1. Run the routine health check.
2. Confirm the macOS account is logged in; a LaunchAgent does not run at the
   pre-login screen.
3. Check for an invalid token, empty allowlist, unreplaced plist path, missing
   trusted CLI, or a second worker holding `logs/telegram/bot.lock`.
4. Restart once. If it immediately fails again, stop it and preserve logs and
   runtime state before changing anything.

### Repeating crash or startup loop

Stop the service to end the loop. Capture the last 200 lines of both output
logs, run `plutil -lint`, `monday status`, and `monday build capabilities`, then
fix the first current error. Do not clear the queue or delivery state as a
general troubleshooting step.

### Provider or coding tool unavailable

Run `monday build capabilities`. Reauthenticate Codex/ChatGPT or GitHub CLI when
their readiness has expired. Claude Code is optional; Codex can be the builder
and mandatory reviewer. API provider outages affect advisory/team work and may
fall through to another configured provider, but the advisory final reviewer
still fails closed without OpenAI. Controller-classified temporary live-build
failures retry automatically; policy, validation, and unclassified failures do
not.

### Build publication is uncertain

Do not submit a second identical request. Inspect the exact delivery:

```bash
monday build history --limit 20
monday build get DELIVERY-ID
monday build observe DELIVERY-ID
```

Also inspect GitHub for the recorded branch or pull request. Preserve the local
delivery directory until the exact identity is reconciled.

### Unauthorized Telegram or GitHub activity

1. Stop the service immediately.
2. Use BotFather to revoke/rotate the Telegram bot token and update `.env`.
3. Revoke or rotate any affected provider credentials and authenticated GitHub
   session using the provider's account controls.
4. Review GitHub branches and pull requests, MondayOS delivery history, and the
   retained output logs. Do not merge unexpected pull requests.
5. Restore only from a known-good source commit and runtime backup, then rerun
   all readiness checks before restarting.

### Disk space is low

Stop new work, back up runtime state, and inspect usage:

```bash
du -sh logs/* 2>/dev/null
df -h .
```

Archive reviewed top-level output logs first. Failed delivery worktrees may be
the only diagnostic copy; never remove one that belongs to an active, retrying,
or publication-uncertain delivery. Preserve `state.json` and delivery records.

## Operating rules

- Secrets stay only in `.env`, authenticated tool storage, or encrypted backup.
- Runtime state stays local and backed up; it is not committed to GitHub.
- Never manually edit `logs/telegram/state.json` or delivery JSON during an
  incident.
- Never run two Telegram workers against the same project root.
- Never resend publication-uncertain work until its delivery identity is
  reconciled.
- A successful MondayOS delivery ends at a reviewed pull request. Merge and
  deployment remain separate operations.
