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
- After a successful live build, durably watches the exact reviewed PR and sends
  restart-safe follow-ups only when its aggregate checks or lifecycle meaningfully
  change. Observation failures back off without exposing remote diagnostics.
- Supports task/system status, active-task listing, and explicit run commands.
- Supports idempotent approval and rejection of reviewed runs.
- Atomically saves each accepted, minimal request and the advanced Telegram
  offset in `logs/telegram/state.json`, then immediately confirms that it was
  saved and reports its FIFO queue position. Unauthorized or otherwise ignored
  content is never queued.
- Keeps polling and acknowledging new requests while one non-daemon worker
  exclusively runs Monday/model work. Commands execute in FIFO order, and
  provider/build concurrency stays at one because a MondayOS instance and its
  providers are intentionally not thread-safe.
- Recovers queued, running, waiting, and retrying work after restart with the
  same Telegram job, task, run, and delivery identities. It retries an
  unexpected poison failure at most three times, then terminalizes that job so
  later work can proceed.
- Removes active request text when a job becomes a terminal replay tombstone.
  A graceful stop finishes the current job and leaves the rest durably queued.

Telegram is the transport only until an accepted request is saved. After that
atomic checkpoint, the local queue—not Telegram's temporary update retention—is
the source of truth for pending work.

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
background pull-request follow-up loop and therefore does not provide ongoing
monitoring. Any saved open-PR follow-up remains durable and resumes the next time
the normal `monday telegram` service starts.

To discover the numeric ID of someone who has messaged the bot:

```bash
monday telegram --identify
```

Identification takes the same single-worker lock as the daemon and never confirms
the pending messages it reads. It refuses to run while the normal worker is live,
so setup cannot steal or drop a real request.

At startup MondayOS verifies the bot identity and removes any prior webhook
without dropping queued updates. Polling and webhooks cannot be used at the same
time, so this ensures one predictable delivery path.

The saved offset, durable FIFO jobs, retries, and replay tombstones in
`logs/telegram/state.json` are bound to the bot's numeric Telegram identity.
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
`/help`. A group `/build` always uses the advisory team even when private live
builds are enabled; `/deliver` is ignored. Task listings and approval decisions
(`/tasks`, `/approve`, `/reject`) remain private-chat-only even when the group
itself is allowlisted.

## Pull-request lifecycle follow-ups

When a private live build reaches `pr-open`, MondayOS checkpoints a request-free
follow-up record before acknowledging the Telegram job. The saved identity fixes
the delivery, task, project, originating chat, and PR URL. Each poll then goes
through the public delivery observer, which also revalidates the saved GitHub
repository, base branch, head branch, PR number and URL, and exact reviewed commit
SHA. The observer is read-only at GitHub: it cannot push, edit, close, merge, or
deploy the pull request.

Telegram receives only the PR lifecycle and one aggregate check state:
`pending`, `passed`, `failed`, or `no-checks`. It never sends or persists
individual check names, descriptions, annotations, logs, or raw observation
errors. Repeated observations are silent; the bot sends a message only for a
meaningful transition, such as checks becoming pending, passed, failed, or absent,
or the PR becoming merged, closed, or divergent from the reviewed SHA.

The follow-up phase, next poll time, and last-notified transition are stored in
the completed job tombstone in `logs/telegram/state.json`, without retaining the
original request text. A restart can reclaim a poll that was in flight. Pending
checks are polled more frequently, settled open-PR states more slowly, and
transient observation failures use exponential backoff capped at five minutes;
Telegram rate limits are honored separately for up to 24 hours. A permanent
Telegram destination failure, such as a blocked bot or deleted chat, ends that
watcher in a request-free `notification-failed` state instead of retrying
forever. Each watcher has a durable 30-day lifetime, and at most 500 can be
active at once. If that capacity is full, the final build message says that
monitoring did not start; the pull request itself is unaffected. Monitoring
also stops after a verified terminal PR state.

Open-PR check results do not change the MondayOS task. Only a verified terminal
state is reconciled: an exact reviewed merge completes an eligible task, while a
closed or SHA-diverged PR returns a task still in `REVIEW` to `IN_PROGRESS`
unless a newer open delivery remains authoritative. A newer open delivery also
prevents an older merged PR from completing the task underneath newer work.
Human-managed states are not overridden. The bot reports that result but still
never merges or deploys it.

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

Restart or stop it with:

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
