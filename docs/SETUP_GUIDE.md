# MondayOS Operator Setup Guide

This guide turns a Mac mini into the always-on MondayOS Telegram control plane.
The primary path is subscription-first: Codex signs in with ChatGPT and performs
both implementation fallback and the mandatory final review. Claude Code is an
optional first-choice builder. API keys are optional and are used only by the
advisory team and AI Workspace provider layer.

The live-build controller creates reviewed GitHub pull requests. It does not
merge or deploy them.

## 1. Prepare the Mac and repository

Requirements:

- macOS on the Mac mini, Python 3.11 or newer, and Git.
- The MondayOS repository in a stable absolute location. Do not move it after
  installing the service because launchd and delivery records use absolute paths.
- A GitHub remote named `origin` whose default branch points to the intended
  repository.
- A private Telegram account and a bot created through the verified BotFather.

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
monday status
pytest
```

`monday status` should report the MondayOS version and healthy modules. Resolve
installation or test failures before enabling the background service.

## 2. Configure subscription-authenticated build tools

The live-build path does not require `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`.
It discovers authenticated tools only from the ChatGPT app bundle and standard
Homebrew/system locations.

1. Install or open Codex, run `codex` from a project directory, and choose
   **Sign in with ChatGPT** on the first run. This is mandatory because MondayOS
   uses a fresh Codex session for the exact-artifact final review. See the
   [official Codex CLI quickstart](https://learn.chatgpt.com/docs/cli). Confirm
   the saved sign-in with `codex login status`.
2. Install GitHub CLI in a standard Homebrew/system location, then authenticate:

   ```bash
   gh auth login
   gh auth status
   ```

3. Optional: install Claude Code in a standard Homebrew/system location and
   sign in. MondayOS self-builds prefer it as the implementation builder when
   available; registered external projects use Codex as their builder. Without
   Claude Code, the ChatGPT-authenticated Codex CLI handles self-builds too.
   Both builders receive only bounded controller-selected files, have model
   tools disabled, and run outside the target worktree.
4. Confirm the exact environment MondayOS can use:

   ```bash
   monday build capabilities
   ```

Do not enable live builds until the report says `ready: True`. DeepSeek can be
an advisory/provider-layer fallback, but it is not a tool-capable coding
executor in the current build workflow.

## 3. Create the Telegram bot

1. In Telegram, open the verified **@BotFather** account.
2. Send `/newbot`, complete the prompts, and copy the token once.
3. Create the local configuration:

   ```bash
   cp -n .env.example .env
   chmod 600 .env
   ```

   If `.env` already exists, keep it and edit it in place; do not overwrite
   working credentials with the template.

4. Put the BotFather token into `TELEGRAM_BOT_TOKEN`. Leave the user-ID
   placeholder in place for the moment.
5. Send `/start` to the new bot, then discover your numeric IDs locally:

   ```bash
   monday telegram --identify
   ```

6. Put the printed positive user ID into
   `MONDAYOS_TELEGRAM_ALLOWED_USER_IDS`. Comma-separate multiple trusted users.
   Group chats are disabled unless their numeric IDs are separately listed in
   `MONDAYOS_TELEGRAM_ALLOWED_CHAT_IDS`.

Only allowlisted users are processed. Private chats are required for live
delivery and approvals. Never paste credentials into a Telegram request.

## 4. Choose the operating mode

Start with:

```text
MONDAYOS_TELEGRAM_LIVE_BUILD=false
```

This runs the advisory team in review mode. After `monday build capabilities`
reports ready, change it to:

```text
MONDAYOS_TELEGRAM_LIVE_BUILD=true
```

With live builds enabled, private plain text and `/build` requests create a
task, build in an isolated worktree, validate and repair it, require ChatGPT
approval of the exact artifact, push a branch, and open a pull request. `/run`
continues to use the advisory team.

Optional provider API credentials belong only in the local `.env`:

```text
ANTHROPIC_API_KEY=your-anthropic-api-key
OPENAI_API_KEY=your-openai-api-key
DEEPSEEK_API_KEY=your-deepseek-api-key
```

Leave those lines unset when only the subscription-authenticated live-build
path is needed. If advisory providers are configured, MondayOS uses role-aware
fallback; the advisory team's terminal reviewer still requires
`OPENAI_API_KEY`. Codex/ChatGPT login and an OpenAI API key are separate forms
of authentication. See [PROVIDERS.md](PROVIDERS.md) for supported provider and
model overrides.

## 5. Run setup smoke tests

Keep the launchd service stopped while testing interactively.

```bash
monday status
monday build capabilities
monday telegram --once
```

Send `/status` to the bot before `monday telegram --once`. A successful smoke
test receives and answers that command, then exits. `--once` is not an always-on
mode and does not run delayed delivery retries or pull-request monitoring.

For a full delivery smoke test after enabling live builds, send a narrowly
scoped documentation request to the bot. Confirm that it:

1. returns a durable `JOB-ID` before the request can start;
2. reports build progress;
3. opens one pull request with a validation and ChatGPT review summary; and
4. stops without merging or deploying.

## 6. Install the always-on launchd service

The repository includes a credential-free template. It reads `.env` at startup.

```bash
mkdir -p logs ~/Library/LaunchAgents
cp deploy/launchd/com.mondayos.telegram.plist.example \
  ~/Library/LaunchAgents/com.mondayos.telegram.plist
```

In the copied plist, replace every `REPLACE_WITH_MONDAYOS_ROOT` occurrence with
the repository's absolute path. There are five occurrences. Do not put tokens,
keys, or other secrets in the plist.

Validate and start it:

```bash
plutil -lint ~/Library/LaunchAgents/com.mondayos.telegram.plist
launchctl bootstrap "gui/$(id -u)" \
  ~/Library/LaunchAgents/com.mondayos.telegram.plist
launchctl print "gui/$(id -u)/com.mondayos.telegram"
```

If the service was already loaded, restart it after changing `.env` or code:

```bash
launchctl kickstart -k "gui/$(id -u)/com.mondayos.telegram"
```

Logs are written to `logs/telegram-stdout.log` and
`logs/telegram-stderr.log`. Operational queue and delivery state lives under
`logs/telegram/` and `logs/delivery/`.

Install the separate credential-free liveness watchdog after the normal daemon
is loaded:

```bash
cp deploy/launchd/com.mondayos.telegram-watchdog.plist.example \
  ~/Library/LaunchAgents/com.mondayos.telegram-watchdog.plist
# Replace all five REPLACE_WITH_MONDAYOS_ROOT occurrences with the same absolute
# repository path used by the daemon plist.
plutil -lint ~/Library/LaunchAgents/com.mondayos.telegram-watchdog.plist
launchctl bootstrap "gui/$(id -u)" \
  ~/Library/LaunchAgents/com.mondayos.telegram-watchdog.plist
launchctl print "gui/$(id -u)/com.mondayos.telegram-watchdog"
```

The watchdog runs once at load and every 60 seconds. It considers only daemon
liveness. Restart eligibility requires a loaded service, a held worker lock,
and a valid heartbeat with an exact `started_at` generation. A missing lock,
missing or invalid heartbeat, or absent `started_at` fails closed for operator
diagnosis and never triggers a restart. For an eligible stopped-daemon,
stale-supervisor, or stuck progress-lane failure, restart requires two matching
samples for that exact generation and unchanged progress evidence 45–150
seconds apart. New progress or a longer sample gap starts a new evidence
window. Before requesting `launchctl kickstart -k`, the watchdog rechecks,
checkpoints the request, and rechecks once more; it confirms success only after
observing a new `started_at`. Restarts have a five-minute cooldown and a maximum of three
requests per hour. Queue pressure, pending receipt/refusal/delivery/lifecycle
outboxes, provider availability, and live-build readiness can make `--health`
unhealthy, but never cause this watchdog to restart the daemon.

Watchdog output is written to `logs/telegram-watchdog-stdout.log` and
`logs/telegram-watchdog-stderr.log`. The plist contains only an explicit
`PATH`; keep tokens and keys in the Git-ignored `.env`.

## 7. Keep the Mac mini awake

In **System Settings → Energy Saver**, enable the setting that prevents
automatic sleep while the display is off. Also enable restart after a power
failure if that option is available. Verify the active policy with:

```bash
pmset -g custom
```

This service is a per-user LaunchAgent. It starts after that macOS account logs
in, not at the pre-login screen. After a restart or power failure, confirm the
account is logged in and run the health check below. Keep the repository and
its `.venv` on a locally mounted disk that is available at login.

## 8. Final readiness check

```bash
launchctl print "gui/$(id -u)/com.mondayos.telegram"
launchctl print "gui/$(id -u)/com.mondayos.telegram-watchdog"
monday telegram --health
monday status
monday build capabilities
gh auth status
tail -n 50 logs/telegram-stderr.log
```

Then send `/status` in the allowlisted private Telegram chat. MondayOS is ready
when both LaunchAgents are loaded, `monday telegram --health` reports healthy,
status responds, build capabilities are ready, GitHub authentication is valid,
and the error log has no current startup loop. Monitoring systems can use
`monday telegram --health --json`; it performs no Telegram network request and
returns a nonzero status when configuration, the daemon supervisor heartbeat,
the independent intake or worker progress lease, inbox capacity, required
live-build capabilities, or any acceptance receipt, capacity-refusal notice, or
live-build delivery notice is unresolved. The human report must show both
`Intake` and `Worker` as ready. A legacy heartbeat after an upgrade is expected
to fail closed until the normal daemon is restarted; `--once` records stopped
runtime state and never advertises daemon health.

## Data and secret boundary

Source code and approved pull-request artifacts belong in GitHub. Secrets and
local runtime state do not:

- Never commit `.env`, tokens, keys, authenticated CLI data, or plist secrets.
- Never commit `logs/telegram/`, `logs/delivery/`, agent run logs, or machine-
  specific `config/projects.json`.
- Back up local runtime state because GitHub does not contain pending queue,
  retry, delivery-worktree, or conversation state.
- Before every commit or push, use `git status --short` and verify that no
  credential or runtime path is staged.

Day-to-day operation, backup, recovery, rollback, and incident procedures are
in [RUNBOOK.md](RUNBOOK.md).
