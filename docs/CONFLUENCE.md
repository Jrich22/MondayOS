# Confluence Publishing

MondayOS can publish its documents — roadmaps, sprint summaries, architecture
docs, release notes, agent workflow reports, research reports — to Confluence.

**MondayOS remains the system of record.** Confluence is a *publishing
destination*, not a source of truth. Content flows one way (MondayOS →
Confluence). The link between a MondayOS document and the Confluence page it
was published to is stored **locally**, on the MondayOS side.

---

## Setup

Publishing needs four environment variables. **Credentials live in the
environment only — never in source or committed files.**

```bash
export CONFLUENCE_BASE_URL="https://your-org.atlassian.net"
export CONFLUENCE_EMAIL="you@your-org.com"
export CONFLUENCE_API_TOKEN="xxxxxxxxxxxxxxxx"   # id.atlassian.com/manage/api-tokens
export CONFLUENCE_SPACE_KEY="ENG"                # default space to publish into
```

Create an API token at <https://id.atlassian.com/manage/api-tokens>. The token
is sent using HTTP Basic auth (`email:token`) over HTTPS and is never logged —
`ConfluenceConfig.redacted()` shows `***set***` in place of the value.

Missing credentials fail clearly before any network call:

```
$ monday publish confluence --file docs/ROADMAP.md
Error: Missing Confluence credentials: CONFLUENCE_BASE_URL, CONFLUENCE_EMAIL,
CONFLUENCE_API_TOKEN, CONFLUENCE_SPACE_KEY. Set them in the environment
(never in source): ...
```

---

## Commands

```bash
# Publish a knowledge entry or a docs/ document by ID
monday publish confluence RES-0007

# Publish an explicit file
monday publish confluence --file docs/ROADMAP.md

# Choose the space and/or a parent page
monday publish confluence --file docs/ROADMAP.md --space ENG
monday publish confluence --file docs/ROADMAP.md --parent 123456

# Preview without publishing (no credentials required)
monday publish confluence --file docs/ROADMAP.md --dry-run

# Explicitly overwrite a specific existing page
monday publish confluence --file docs/ROADMAP.md --update-page 123456

# Republish even if the content is unchanged
monday publish confluence --file docs/ROADMAP.md --force

# Show local publish history
monday publish history
```

### Resolving `DOC_ID`

`DOC_ID` is resolved from the MondayOS system of record in this order:

1. a **knowledge entry** with that ID (its title + body are published), then
2. a **file** — `<DOC_ID>`, `docs/<DOC_ID>.md`, or `<DOC_ID>.md` under the
   project root.

`--file PATH` bypasses resolution and publishes the file directly. The page
title defaults to the document's first Markdown heading, or `--title` if given.

---

## Create vs. update — the safety model

- The first time a document is published, a **new page** is created. Its
  MondayOS-doc-ID → Confluence-page-ID mapping is stored locally.
- On subsequent publishes, MondayOS **updates the same page** using that stored
  mapping — it never creates duplicates.
- A page is only ever overwritten when the target is already known: either a
  **mapping exists**, or **`--update-page PAGE_ID`** was supplied explicitly.
  With neither, MondayOS creates a new page rather than guessing.
- If the source content is **unchanged** since the last publish (matched by
  SHA-256 checksum), the update is skipped as `up-to-date`. Use `--force` to
  republish anyway.
- **`--dry-run`** previews the decision (create vs. update, target page, space,
  whether content changed, storage size) and makes **no** network calls — so it
  works without credentials.

---

## What gets recorded

Every publish is recorded locally under `logs/publish/confluence.json`:

- **`pages`** — the current mapping, one record per document:
  source file / knowledge entry, Confluence page ID, page URL, space key,
  content checksum, last-published timestamp, status, and Confluence version.
- **`history`** — an append-only log of every attempt (create, update,
  up-to-date, dry-run, failed), shown by `monday publish history`.

---

## Markdown conversion

Markdown is converted to Confluence **storage format** (an XHTML dialect) by a
small, dependency-free converter (`integrations/confluence/converter.py`). The
first version supports the constructs MondayOS documents use:

| Markdown | Confluence storage |
|---|---|
| `# … ######` | `<h1>…<h6>` |
| paragraphs | `<p>…</p>` |
| `- item` / `* item` | `<ul><li>…</li></ul>` |
| `1. item` | `<ol><li>…</li></ol>` |
| ` ```lang … ``` ` | code macro (`<ac:structured-macro ac:name="code">`) |
| `\| a \| b \|` tables | `<table>…</table>` |
| `[text](url)` | `<a href="url">text</a>` |
| `**bold**`, `*italic*`, `` `code` `` | `<strong>`, `<em>`, `<code>` |

HTML-significant characters (`&`, `<`, `>`) are escaped. Anything unsupported is
carried through as paragraph text.

---

## Testing / demos without a real account

Set `MONDAYOS_CONFLUENCE_FAKE=1` to route publishing to an in-memory fake
client that never touches the network (state persists to
`logs/publish/fake-confluence.json` so create-then-update works across CLI
runs):

```bash
MONDAYOS_CONFLUENCE_FAKE=1 CONFLUENCE_SPACE_KEY=ENG \
  monday publish confluence --file docs/ROADMAP.md
```

The test suite (`tests/test_confluence.py`) uses the fake client and the
`MONDAYOS_CONFLUENCE_FAKE` seam exclusively — **no test makes a live API call
or requires a real Confluence account.**

---

## Architecture

```
integrations/confluence/
  config.py      ConfluenceConfig.from_env(), credential checks (no secrets in source)
  converter.py   Markdown → storage-format XHTML
  client.py      ConfluenceClient: HttpConfluenceClient (stdlib) + FakeConfluenceClient
  mapping.py     PublishStore: local doc-ID → page-ID mapping + history
  publisher.py   ConfluencePublisher: create/update/dry-run + safety + recording
```

The integration reads no MondayOS internals: the `Monday.publish()` API resolves
a document to content and hands the publisher a plain `PublishDoc`. This keeps
the boundary clean — the publishing target knows nothing about how MondayOS
stores its documents.
