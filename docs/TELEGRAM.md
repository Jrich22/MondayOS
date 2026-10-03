# MondayOS Telegram Control Plane

Telegram is MondayOS's first remote control surface for the Mac mini. It uses
outbound long polling, so it does not open a public port or require a tunnel.

## What this increment does

- Accepts normal private text as a build request.
- Creates a durable MondayOS task with Telegram provenance.
- Assigns the task to the MondayOS agent team in review mode.
- Reports team and stage progress back to the originating chat.
- Supports task/system status, active-task listing, and explicit run commands.
- Supports idempotent approval and rejection of reviewed runs.
- Recovers safely after restart without creating a second task for the same
  Telegram update. An interrupted team run is marked and one replacement run is
  linked to the same durable Telegram job.
- Retries unexpected handler failures three times, then preserves a dead-letter
  checkpoint so one poison update cannot block every later request.

The Telegram worker is sequential because a MondayOS instance and its providers
are intentionally not thread-safe. Telegram retains queued updates while the
worker is busy, but Telegram's Bot API only retains them for up to 24 hours. This
first increment therefore should not be left offline for more than a day; a later
queue-worker split will persist incoming requests immediately while long builds
run separately.

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

For one polling cycle during setup:

```bash
monday telegram --once
```

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

The saved offset, retry jobs, and replay tombstones are bound to the bot's
numeric Telegram identity. Renaming the same bot keeps its offset. Replacing the
token with a different bot clears the old bot's update state before polling, so
one bot's offset cannot silently discard another bot's pending messages.

## Commands

```text
normal text             create and run a task
/build REQUEST          create and run a task
/run TASK-ID            run an existing task
/status [TASK-ID]       system or task status
/tasks                  list active tasks
/approve RUN-ID         approve a reviewed run
/reject RUN-ID REASON   reject a reviewed run
/help                   show help
```

Only allowlisted Telegram users are processed. Unauthorized messages are ignored
without revealing whether the bot controls MondayOS. Group messages are ignored
unless the chat is separately allowlisted, and group chatter never becomes a
task implicitly. In an allowlisted group, a command addressed with
`@bot_username` runs only when that username matches this verified bot; commands
addressed to another bot are ignored.

Allowlisted groups intentionally expose only `/build`, `/run`, `/status`, and
`/help`. Task listings and approval decisions (`/tasks`, `/approve`, `/reject`)
remain private-chat-only even when the group itself is allowlisted.

## Always-on Mac mini service

An example launchd definition lives at
`deploy/launchd/com.mondayos.telegram.plist.example`. Replace all five occurrences
of its root-path placeholder, copy it to
`~/Library/LaunchAgents/com.mondayos.telegram.plist`,
and load it with launchd. It contains no credentials; `monday telegram` reads the
Git-ignored project `.env` at startup.

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
an empty user allowlist, an unreplaced plist path, or another worker already
holding `logs/telegram/bot.lock`.

## Current boundary

This slice provides the remote control plane and runs the existing MondayOS team
workflow. That workflow produces planned/reviewed agent output and a final review
gate that still requires human approval in this increment. It does **not** yet
edit code in the terminal, run the ChatGPT repair loop, commit, push, merge,
deploy, or transcribe voice. Those are subsequent increments that will reuse this
same durable Telegram request path.
