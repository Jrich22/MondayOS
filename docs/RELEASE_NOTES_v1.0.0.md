# MondayOS v1.0.0

**The release that makes MondayOS's answers checkable.**

v1.0 is not about new surface area. It is about a single property: when MondayOS
tells you something, you can verify it — and when it cannot verify itself, it
says so instead of guessing.

---

## What changed

### Answers cite evidence that exists, or they are not shown

Every identifier an answer states — a commit, an ADR, a task, a file, a symbol,
a pull request — is checked against two authorities before it reaches you: the
project's own records, and the evidence retrieval actually supplied. An answer
carrying an identifier that resolves against neither is regenerated **once**,
told exactly which references failed. If the second attempt also fails, the
answer is not shown at all:

> MondayOS could not verify the evidence cited in this answer, so it has not
> been shown.

The unverified identifiers never appear in that message. A citation you cannot
follow is a citation nobody verifies.

**Structured citations.** Retrieval hands the model opaque handles — `[E1]`,
`[E2]` — and resolves them back to real identifiers after generation. A handle
that names nothing is caught as a fabricated reference rather than rendered to
you as one. Grouped citations (`[E2, E3]`) resolve every member independently,
and one unknown member invalidates the whole group: a half-resolved citation is
worse than an unresolved one, because it looks checked.

**Streaming holds the guarantee.** An evidence-bearing answer is buffered until
it has been verified, then released whole. No amount of post-generation checking
un-shows a citation someone has already read.

### MondayOS computes the scores; the model reports them

Confidence, evidence strength and execution risk are computed by MondayOS's
reasoning layer from evidence, and supplied to the model as conclusions to
explain. The model may report them, round them, or describe them in words. It
may not invent one.

This is enforced, not merely requested. Every numeric reasoning score in a
generated answer is checked against the values MondayOS actually computed or
persisted for that turn — which values are authoritative depends on the kind of
turn, and a reassessment deliberately withholds the superseded numbers so an
answer cannot quote them back as current. An unsupported score triggers one
correction naming the offending number and the allowed set; a second failure is
refused. The model is never asked whether its own number is valid.

In the release acceptance run, a frontier model wrote *"approximately 60-70%,
though MondayOS did not compute this figure."* It was rejected, corrected,
refused, and never reached the user or the conversation store.

### Timeouts separate connecting from generating

Reaching a provider and waiting for it to write are different waits. Connection
uses a short fixed timeout; generation uses a deadline derived from the token
budget and the provider's throughput, clamped to a sane range. A provider that
is down fails fast; a long executive answer is given time to arrive.

### Project isolation is enforced, not assumed

Conversations, context, git history, decision records and strategic state are
scoped to one project. The acceptance run recorded zero cross-project citations,
zero foreign commit references and zero invented ADRs across 52 turns and four
projects.

### Reasoning state is durable and inspectable

Strategic recommendations persist, follow-ups continue them rather than silently
re-deciding, staleness is detected when the project moves, and the same question
produces the same recommendation. No prompt, no context snapshot and no rejected
candidate is ever written to persisted state.

---

## Verified by

A 52-turn acceptance journey across four real projects on
`anthropic · claude-sonnet-4-5`, plus a streaming journey per project, scored by
fourteen gates.

- 0 provider incidents · 251 score claims validated · 2 unsupported, both blocked
- 0 cross-project citations · 0 invented ADRs · 0 foreign commits · 0 invalid
  citation lines · 0 invented initiatives · 0 hidden reasoning persisted
- 2,643 tests · benchmark gate green with zero drift

Full signoff, including the adjudication of the one failing gate:
`reports/v1_release_signoff.md`.

---

## Known limitations

Read `docs/KNOWN_LIMITATIONS.md` before filing a bug — four behaviours are
deliberate and documented.

The short version: **MondayOS is conservative.** It would rather withhold a
correct answer than show an unverified one. Three of the four known limitations
are cases where it refuses something that was actually fine — most notably a
citation of the form `ClassName.method_name`, which it does not yet resolve and
therefore declines. Roughly one turn in twenty was refused this way in the
acceptance run.

Score validation is a heuristic backstop behind a computed-score architecture,
not a proof. One known shape — a range such as `94–95%` — is not recognised as a
claim. It was never observed to deliver a wrong number, and an independent audit
now reports anything the validator misses.

## Upgrading

No migration. `1.0.0` succeeds `1.0.0b1` with no schema, storage or
configuration change.
