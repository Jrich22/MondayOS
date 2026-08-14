# sourcingBOT — Architecture Decision Records

Newest first. Each records the decision, why, and what it costs.

---

## ADR-019 — Fit score authority stays in the sourcingBOT domain

**Status:** accepted · approved 2026-08-14 · TASK-0061

**Context.** In agent-operated sourcing, Claude evaluates every profile against
the brief. The obvious shape is for it to return a fit score alongside its
reasoning, and for sourcingBOT to store what it was given.

**Decision.** It does not. **Claude supplies evidence; sourcingBOT computes the
number.**

| Claude supplies | sourcingBOT computes |
|---|---|
| per-requirement assessments (`yes` / `no` / `unknown`) | `fitScore`, via `withFitScore()` |
| rationale for this req | rank position |
| relevant experience | whether ADR-006's cap applies |
| concerns and gaps | |

`upsert_working_shortlist_entry` has **no `fitScore` parameter**. A score sent
anyway is ignored, and a test asserts the parameter's absence.

**Why.** A score is not an observation, it is the output of a rule — the brief's
weights, and ADR-006's rule that an unmet required requirement caps fit at 0.
Accepting the number from outside moves that rule out of the domain, where three
things follow. It stops being checkable: nobody can re-derive 78 from the
evidence. It stops being consistent: two profiles with identical assessments can
score differently depending on how the sentence was phrased. And it drifts under
pressure — a long session hunting the twentieth candidate is exactly the
condition under which "good enough" quietly becomes 72.

Keeping the computation inside also means re-ranking is free and always correct.
Editing a requirement weight re-scores every working entry with no round trip.

**Alternatives rejected.** *Accept Claude's score and store both* — two numbers
that disagree, and no rule for which wins. *Accept the score and validate it
against the assessments* — that is computing it, with extra steps and a
disagreement to resolve. *Let Claude propose and a human confirm* — makes the
recruiter arbiter of arithmetic they cannot see.

**Consequences.** Claude must return assessments covering the brief's
requirements; a sparse assessment set yields a low or null score rather than an
optimistic one, which is the correct failure direction. Entries assessed against
an older `briefVersion` keep being scored against **that** version (ADR-003) —
re-scoring old evidence against a new brief would fabricate judgements nobody
made. Finalization surfaces such entries rather than silently recomputing them.

---

## ADR-018 — Shortlist-first sourcing: five states, one promotion

**Status:** accepted · approved 2026-08-14 · TASK-0061, TASK-0062

**Context.** Manual capture assumed the recruiter records each person they
review, so "reviewed" and "recorded" were the same act. Agent-operated sourcing
breaks that: Claude may review 137 profiles to find 20. Persisting each one as a
`Candidate` would fill a permanent talent pool with people nobody selected, and
would make the pool a by-product of search volume rather than of judgement.

**Decision.** A profile moves through five states, and only the last is
permanent:

```
reviewed ──▶ potential ──▶ working shortlist ──▶ finalized ──▶ Candidate
(counter)   (session,      (session-scoped      (human       (+ReqCandidate)
             capped 25)     entry, ranked)       action)
```

| State | Stored | Personal data | Lifetime |
|---|---|---|---|
| **reviewed** | aggregate counter | **none** | session |
| **potential** | `SkippedCandidate`, cap **25** | name + reason | session |
| **working shortlist** | `WorkingShortlistEntry` | full evidence | session |
| **finalized** | `Candidate` + `ReqCandidate` | full record | permanent |

**The working shortlist is session-scoped, and that is the load-bearing choice.**
Claude may hold more contenders than the target, swap a weaker entry for a
stronger one, and change its mind repeatedly — none of which touches the
permanent pool. Finding candidate #21 neither creates a `Candidate` nor deletes
one; it reorders a list that lives on the session. The talent pool only ever
grows by a human act.

**Finalize Shortlist is a human action, and cannot be an MCP tool.** There is no
`finalize_shortlist` tool, and a test asserts its absence. Three reasons, in
order of weight:

1. **It creates permanent records about real people.** Every other write in this
   system is provisional and session-scoped; this one is not, and the boundary
   between provisional and permanent is exactly where a human belongs.
2. **It is the acceptance of a recommendation.** Claude proposing twenty people
   and Claude accepting its own twenty people are different acts, and collapsing
   them leaves nobody having chosen.
3. **It is where duplicate resolution lands.** Merging two records for one human
   is the failure mode ADR-012 exists to prevent, and it is a judgement, not a
   match score.

Claude may call `report_target_reached`. That is a report, not a state change.

**Finalization operates on the top N *or* on a recruiter-selected subset.**
Both run the identical transaction — freeze, validate, resolve duplicates,
build, single atomic commit — and the only difference is which entries are
frozen. Selection is a UI act; there is no MCP path to either form.

The subset exists because the top-N-only rule was wrong in practice. A recruiter
who runs a search, finds two excellent people and concludes the market is thin
had exactly two options: promote a shortlist of twenty they did not believe in,
or promote nothing and lose the two. Both are bad, and the second is worse — it
punishes honest judgement about a weak market. A search that yields three good
people is a legitimate outcome, not a failed run, and the model should be able
to say so.

What subset selection explicitly is **not** is a per-person promote button.
There is no path that takes one entry straight to the pool, because that is
finalization with the duplicate check skipped — the exact shortcut ADR-012 was
written to prevent. Selecting one entry and finalizing is fine; it runs the
whole transaction. **Finalize Shortlist remains the one canonical way a person
becomes permanent**, and its guarantees do not vary with how many were chosen.

Unselected entries are untouched: they stay session-scoped evidence, are neither
deleted nor rewritten, and remain available if the recruiter finalizes again
later. Sessions record how many were finalized against the target, so a session
that promoted 3 of 20 reads as a deliberate outcome rather than an unfinished
one.

**Rejected profiles are counted, never named.** No `Candidate`, no
`ReqCandidate`, no stored name or profile reference. Recording a name and a
rejection reason for several hundred people who never entered the pipeline builds
a profiling dataset about individuals who never applied — in a product whose
posture is human oversight. Counting them costs nothing and answers every
question the recruiter actually asks. The 25-cap on near-misses holds the same
line: "who did I nearly take?" stays answerable without becoming a dossier.

**Alternatives rejected.** *Persist every strong profile as a Candidate and rank
a view over them* (the first proposal) — makes pool size a function of search
volume, and leaves the recruiter deleting people to tidy up. *Hard-cap the
working shortlist at N with eviction* — an entry is evidence a profile was
evaluated; evicting silently discards work Claude did. Capping the **final**
promotion is the right place. *Let Claude finalize once the target is met* — see
above. *Store rejected profiles for later mining* — the value is speculative, the
data-protection surface is not.

**Consequences.** A session carries real weight now: up to 25 near-misses and a
working shortlist of ~25 entries with full evidence. Sessions become the largest
records in the store, which is a persistence consideration for ADR-016 but not a
model problem. An abandoned session leaves its working shortlist unpromoted —
correct, since nobody accepted it, and it remains readable as evidence of what
was searched. Reviewed counts come from a counter rather than from summing
records, so a legacy session's counts derive from its captures as before. And
because entries are session-scoped, a recruiter who wants one of them
permanently must finalize — there is deliberately no "promote just this person"
side door, because that is finalization with the duplicate check skipped.

---

## ADR-017 — Attestations are human-only, and presence is enforced rather than promised

**Status:** accepted · approved 2026-08-13 · TASK-0059, TASK-0061

**Context.** Once Claude can write to sourcingBOT over MCP, the supervision
record stops being self-evidently true. A `SourcingSession` asserts that a named
human initiated and supervised a search. If the agent can create that record, the
assertion is worth nothing — the system would be attesting to its own oversight.

The first draft of the MCP surface included a `start_session` tool. That was
wrong, and it is worth naming why: it is exactly the failure mode MondayOS
TASK-0055 removed from reviewer verdicts, where a missing or malformed verdict
defaulted to `pass`. A tool that lets the agent supply `operator` and
`acknowledgedPolicy` is a fail-open default wearing a parameter list.

**Decision.** Two rules, both mechanical.

**1. Attestations are UI-only.** `start`, `resume`, and `complete` exist only in
the interface, performed by a human. Over MCP, Claude may move a session toward
*more* restriction — `pause` and `halt` — and never toward less. There is no
`start_session` tool, and its absence is asserted by a test rather than left to
reviewer vigilance.

Claude may never assert operator identity or policy acknowledgement on the
human's behalf. `resume` is included in the human-only set because resuming is
re-attesting: it is the same claim, made again, about a session that stopped.
`complete` is included because closing the record is what fixes its counts and
its meaning.

**2. Presence is enforced.** "No unattended sourcing" is unenforceable as
written — a session left open overnight looks identical to a supervised one.
`SourcingSession.lastOperatorConfirmationAt` and a **15-minute presence window**
make it checkable. Every MCP write requires `operatorPresent(session, now)`;
outside the window the write fails with `operator_absent` **and the session
auto-pauses**. An explicit "I'm still here" control refreshes it, as do
designated ordinary UI interactions.

The auto-pause is the load-bearing half. Refusing the write alone would leave a
session that still claims to be live while nobody is watching it; pausing makes
the record match reality without a human having to notice.

**Related: `halted` is not `paused`.** A pause is the operator's choice. A halt
is the platform telling you something — a warning, a restriction, a checkpoint,
an unexpected interstitial — and it is raised by whoever sees it first,
operator or agent. They are separate states because collapsing them would let the
most important event in a session be indistinguishable from stepping out for
coffee. Resuming from `halted` requires a fresh acknowledgement; resuming from
`paused` does not.

**Alternatives rejected.** *Let Claude start sessions with the operator name
passed through* — the name proves nothing about who typed it. *A heartbeat the
agent sends* — the agent is not the thing whose presence is in question.
*Continuous webcam or focus detection* — disproportionate, and it measures
attention rather than the accountability the record actually needs. *Refuse
writes on expiry without pausing* — leaves a live-looking session with nobody
present, the exact misrepresentation this ADR exists to prevent.

**Consequences.** A recruiter who steps away mid-session comes back to a paused
session and one extra click; that friction is the feature, and 15 minutes is a
first guess that should be revisited against real usage. Claude cannot clean up
after itself by completing a finished session, so stale in-progress sessions are
possible — the presence auto-pause is what keeps them from claiming to be live.
`proposedBy` and `decidedBy` are recorded separately on every capture and skip,
so "Claude suggested, I decided" stays legible in the record forever, which is
the only form of this claim that survives an audit.

---

## ADR-016 — One local host owns persistence; the SPA and MCP are both clients

**Status:** accepted · approved 2026-08-13 · TASK-0060, TASK-0061

**Context.** sourcingBOT's workspace lives in `localStorage`. An MCP server is a
Node process and cannot reach it. Any design where Claude reads a brief or writes
a candidate needs the data somewhere both can see, and the SPA needs to update
live as those writes land.

**Decision.** A single local process, `sourcingbot-host`, owns
`~/.sourcingbot/workspace.json` and is the only writer. It serves the SPA over
127.0.0.1 HTTP plus SSE, and serves Claude Code over stdio MCP. Both are clients
of the same process; neither owns the file.

**Single-writer is the whole point.** Two processes appending to one JSON file
lose updates — not rarely, but whenever two writes interleave. Making the host
the sole writer removes that class of bug structurally instead of guarding
against it. SSE follows from the same choice: the one writer already knows when
state changed, so the SPA is pushed to rather than polling.

**The host gets no privileged write path.** It calls the same
`captureCandidate` → `commitCapture`, `recordSkip`, `pauseSession` the UI calls.
This is the `QuickSession` rule again — no caller gets a shortcut, least of all
the automated one — and it is what makes the MCP surface safe to extend: a new
tool cannot invent a way to write that the domain would refuse. The existing
`lib/*` modules are already React-free and pure, which is why the host can import
them unchanged.

**Degradation is explicit.** With no host running, the SPA falls back to
`localStorage` and `ManualProvider` and says so on screen. A silent fallback
would let a recruiter work for an hour in a store Claude cannot see.

**Alternatives rejected.**

*File System Access API, no process* — genuinely viable, and the fallback if the
host proves heavy: the SPA holds a directory handle, Claude reads and writes
files. Rejected because it is Chromium-only, needs a re-granted permission,
polls, and enforces no schema at the boundary — and because MCP is the mechanism
Claude Code actually supports.

*Two writers with file locking* — advisory locks on a JSON blob are a lot of
correctness risk to avoid one process.

*A real database* — premature. One JSON document matches the current data volume,
stays inspectable by hand, and the store seam makes it replaceable later.

*Remote MCP server* — would reach claude.ai and Cowork too, but puts a recruiting
workspace on a network for a workflow that is local by nature.

**Consequences.** sourcingBOT stops being a pure browser app; there is now a
process to start, and the README must say so. Writes are atomic (temp file +
rename) and a corrupt file makes the host refuse to start rather than silently
reseeding — losing real sourcing work to a parse error is the worst outcome
available here. Concurrency is document-level and optimistic: stale writes are
rejected, never merged. The loopback bind is the only access boundary; the host
claims no authentication, and a future multi-user version needs a different
design rather than a flag.

---

## ADR-015 — Agent-operated LinkedIn sourcing is accepted with known policy risk

**Status:** **accepted with explicit, documented risk.** Approved by the product
owner 2026-08-13, having been shown the finding below. Binding on every
increment that touches agent-operated sourcing.

**Context.** The target workflow has Claude, via the Claude-in-Chrome extension,
operate an open LinkedIn Recruiter session alongside a supervising recruiter:
proposing filters, reading visible results, opening profiles, and evaluating
them against the approved brief.

An investigation of the actual integration boundary (2026-08-13) established two
things.

**First, the mechanism is real and supported by Anthropic.** The Claude in Chrome
extension exposes browser control to Claude Code as an MCP server
(`claude-in-chrome`), documented at code.claude.com/docs/en/chrome. It runs in a
visible window, reuses the human's existing login, and pauses for logins and
CAPTCHAs. sourcingBOT can expose its own MCP server alongside it. No browser
security boundary is crossed, no credential is handled by us, and — importantly —
**no browser automation code enters this codebase.** Claude Code drives the
browser; sourcingBOT only records decisions.

**Second, LinkedIn prohibits it.** The
[Prohibited software and extensions](https://www.linkedin.com/help/linkedin/answer/a1341387)
page and [User Agreement §8.2](https://www.linkedin.com/legal/user-agreement) ban:

> third party software, including "crawlers", bots, **browser plug-ins, or
> browser extensions that scrape, modify the appearance of, or automate activity
> on LinkedIn's website**

**The policy contains no exception for human supervision.** This is the load-
bearing sentence of this ADR. Every control this product has built — named
operator, per-session acknowledgement, visible browser, human decision on every
profile, immediate halt on warnings — is a genuine *ethical* control and none of
them satisfies LinkedIn's rule, because LinkedIn is not prohibiting *unsupervised*
automation. It is prohibiting *extension-driven activity*. A supervised extension
is still an extension.

The extension does not special-case LinkedIn: its bundle contains no blocklist
covering it. Technical possibility is not permission, and this ADR exists so that
distinction is never quietly lost.

**Decision.** Build it, with the risk recorded here rather than absorbed
silently, and under four constraints:

1. **The domain model stays provider-neutral.** No LinkedIn-specific concept
   enters `Candidate`, `ReqCandidate`, or `SourcingSession`. The channel is a
   `providerId` on the session (ADR-016).
2. **The acknowledgement must describe what actually happens** — see the
   correction below.
3. **A halt is a first-class session state.** Any platform warning, restriction,
   checkpoint, or unexpected interstitial stops the session immediately and
   requires a human to resume it.
4. **The prohibitions hold unchanged.** No unattended or scheduled sourcing, no
   rate-limit bypass, no CAPTCHA bypass, no automation-evasion, no bulk export of
   profiles nobody opened. `supportsCapability()` still returns `false` for every
   entry in `PROHIBITED_CAPABILITIES`.

**The correction this forces.** The current `SUPERVISION_POLICY` has the operator
attest:

> "I will open and review each profile myself; sourcingBOT will not browse for me."

Under an agent-operated session **that sentence becomes false.** Continuing to
show it while Claude drives the browser would produce a signed record asserting
human conduct that did not occur — precisely the misrepresentation the
supervision boundary was built to prevent (ADR-004). The acknowledgement text is
therefore **per provider**, and the agent-operated text must state plainly that
Claude operates the browser, that the recruiter remains present throughout, that
the recruiter makes every add/skip decision, and that the recruiter has been
informed of the terms-of-service risk. Weakening the boundary is not the failure
mode here; *keeping the old wording* would have been.

**Alternatives rejected.**

*Claude never touches LinkedIn (Tier 0)* — zero exposure, and it still removes
the copy/paste problem, but it gives up search construction and result triage,
which is most of the value.

*Claude reads only the profile the human opened (Tier 1)* — materially lower
exposure, since no navigation or pagination is automated, and it was the
recommended option. Rejected in favour of the fuller workflow with the risk
accepted knowingly.

*Recruiter System Connect* — LinkedIn's sanctioned partner API for ATS
write-back. This is the only path that makes the workflow *licensed* rather than
tolerated, and it remains the correct destination. Rejected for now only because
it requires a partner agreement that does not exist yet; ADR-016 exists to make
that migration cheap when it does.

*Do it and say nothing* — the option this ADR exists to foreclose.

**Consequences, stated plainly.**

- **LinkedIn may restrict or terminate the account** used for an agent-operated
  session. Recruiter seats are contracted corporate licenses, so enforcement
  lands on the client's account, not an anonymous one. This is the concrete cost
  and it is not hypothetical.
- The product cannot honestly market this as compliant. It is a supervised,
  disclosed, human-present workflow that runs against LinkedIn's stated terms.
- Any customer-facing deployment needs the customer to make this decision for
  themselves, informed, in writing. A risk the vendor accepted does not transfer
  to a client who was never told.
- `ManualProvider` must remain fully functional and never become a degraded
  path, because it is the fallback if LinkedIn enforces or the policy position
  changes (ADR-016).
- This ADR is reviewed if LinkedIn's terms change, if enforcement is observed, or
  if RSC access is obtained — whichever comes first.

---

## ADR-014 — The Candidate Workspace is home, and leads with conclusions

**Status:** accepted · Candidate Workspace

**Context.** The talent surface was a candidate list at `/candidates`, and `/`
was the requisition list. Both are, structurally, tables. A recruiter opening
the product landed on records and had to derive their own next action.

**Decision.** The Candidate Workspace becomes `/`; the Req Workspace moves to
`/reqs`. The page is ordered **conclusions first, records last**:

1. **Pulse** — six counts, one dense strip
2. **Recommended focus** — a ranked, reasoned worklist
3. **Talent intelligence** — concentration that filters the pool
4. **Activity** — one merged feed
5. **Talent pool** — the table, last, 8 rows by default

Two rules hold the design together:

- **Every number is a doorway.** No statistic renders without a link to the work
  it counts. That is the line between a command center and a vanity dashboard.
- **Every focus row states why it surfaced.** A worklist without reasons is just
  a differently-shaped list, and a recruiter cannot triage it.

**Alternatives rejected.** *Keep the table as the landing view and add widgets
above it* — the table still dominates by sheer height, and the widgets become
decoration. *Charts for concentration* — a chart answers "where is our talent"
and then abandons the user; a clickable bar answers it and takes them there.
*Six large stat cards* — vertical space spent on orientation is space taken from
the worklist, which is the actual product.

**Consequences.** The home route changed, so bookmarks to `/candidates` break
(now `/candidates/:id` for profiles only) and `Candidates.tsx` was retired —
its table lives on inside `TalentPool`. Focus ranking is heuristic and will need
tuning against real recruiter behaviour; the thresholds (fit ≥ 70, pipeline < 3,
capture rate < 25% over ≥ 5 reviewed) are first guesses, documented here so they
are visibly assumptions rather than physics.

Intelligence lives in `lib/intel.ts`, which — like `readiness.ts` (ADR-008) —
stores nothing and composes existing domain functions. `concentration("company")`
delegates to the tested `talentConcentration` rather than reimplementing it.

### Appendix — focus thresholds and their review trigger

The Recommended Focus worklist uses five numeric thresholds. **They are unchanged
by this appendix.** It exists so they are revisited on a schedule rather than
quietly becoming permanent.

| Threshold | Current value | Surfaces |
|---|---|---|
| Strong candidate | fit score **≥ 70** | An unactioned candidate worth chasing |
| Unactioned stages | `identified`, `reviewing` | Which stages count as "not acted on" |
| Thin pipeline | **< 3** live candidates | An open req needing sourcing |
| Low capture rate | **< 25%** | A session worth examining |
| Minimum sample | **≥ 5** reviewed | Below this, too little signal to judge a session |

**Review trigger — approved 2026-08-13.** Revisit all five after **30 days of
real recruiter usage OR 100 candidates reviewed across sessions, whichever comes
first.**

Why a trigger rather than a note. These numbers were chosen with no usage data
at all; they are a starting position, not a finding. Documenting them as
"tunable" makes that honest but changes nothing on its own — thresholds like
these become permanent by default, and the recruiter never learns that a
number, rather than their pipeline, decided what they saw first.

What the review should ask: are strong candidates being surfaced that recruiters
ignore (threshold too low), or are they finding good candidates the worklist
never raised (too high)? Is "thin pipeline" firing on reqs that are healthy for
their market? Is a 25% capture rate actually unusual, or normal for a scarce
skill set?

Until that review, the values above stand.

---

## ADR-013 — Candidate enrichment is review-before-apply

**Status:** **accepted as a product rule; NOT implemented.** Approved
2026-08-13. Binding on any future increment that touches this.

**Context.** Reusing an existing Candidate during capture deliberately does not
update their record (ADR-012, approved). If the operator learns a new email or a
job change while sourcing, that information is currently discarded.

Discarding it is right in the sense that matters — a hasty later capture must
not clobber facts established by a more careful earlier one — but it is
obviously not the end state. Real information is being thrown away.

**Decision.** When sourcingBOT eventually handles this, enrichment must be
**review-before-apply**:

1. A reused Candidate encountered with new information produces a **proposed
   change**, not a write.
2. Proposals surface to the recruiter showing the current value, the proposed
   value, and where the proposal came from.
3. The persistent record changes **only** on explicit recruiter approval.
4. Silent overwrite of a persistent Candidate fact is not acceptable in any
   increment.

**Rationale.** The persistent-person model (ADR-002) is only worth having if the
person record is trustworthy. A record that can be silently rewritten by whoever
sourced most recently is not a durable fact — it is a cache of the last
operator's typing speed. Requiring approval keeps the pool authoritative and
gives the recruiter the one thing an automatic merge cannot: the chance to say
"no, that's a different Priya Raman."

This is the same principle as ADR-011 (skips do not create people) and the
LinkedIn boundary itself: **the system proposes, a human disposes.**

**Not implemented.** No proposal entity, queue, or UI exists yet. Capture
currently discards the new information, which is the safe default until this
lands. Scheduled against Increment 4 — see ROADMAP `SB-2`.

---

## ADR-012 — Reuse from the pool is exempt from the capture origin check

**Status:** accepted · TASK-0057 · **refines the supervision boundary**

**Context.** `recordManualCapture` refused any candidate whose
`origin !== "supervised-linkedin"`. That guard exists so a bulk import cannot be
laundered through a session to look human-reviewed.

Increment 3 made it fire on a legitimate case. Someone who entered the pool as a
`referral` two years ago and is sourced for a new req today **was** genuinely
reviewed by the operator during this session — but `Candidate.origin` records
how a person *first entered the pool*, which is a different fact from whether
*this capture* was supervised. Tests caught it: five reuse cases failed.

**Decision.** `recordManualCapture` takes an explicit
`{ reusedFromPool: true }` option. The origin check applies to **newly created**
candidates only; reuse is permitted but must be declared by the caller.

**Alternatives rejected.** *Rewrite `origin` to `supervised-linkedin` on reuse* —
destroys real provenance about how someone entered the pool, to satisfy a check
about something else. *Drop the origin check* — removes the laundering guard
entirely. *Infer reuse from whether the candidate id already exists* — makes the
exemption implicit and therefore reachable by accident.

**Consequences.** One more parameter at the boundary, and reuse is now a named,
auditable case rather than a silent one. A test asserts a new non-supervised
candidate is still refused, that reuse does not rewrite origin, and that reuse
is still refused when the session is not in progress.

---

## ADR-011 — Skips are session-scoped and never create a Candidate

**Status:** accepted · TASK-0057

**Context.** Operators review far more people than they capture. Tracking the
rejects is valuable — especially the near-misses, which are the first place to
look when a pipeline thins out. The obvious implementation is a `Candidate` with
a `rejected` status.

**Decision.** A skip is a `SkippedCandidate` recorded **on the session**: name as
typed, reason, a `closeCall` flag, timestamp. No `Candidate` is created and no
profile data is stored.

**Rationale.** A skip is a judgement made inside one search, not a durable fact
about a human. Minting a persistent record for everyone glanced at would fill
the talent pool with people nobody evaluated, corrupting the concentration
analytics that ADR-002's persistent-person model exists to enable. It would also
store data about people who were explicitly *not* selected, which is a
proportionality problem as much as a modelling one.

**Consequences.** A skipped person is not searchable in the talent pool, and
skipping the same person in two sessions records two entries. Both are accepted:
`closeCallsFor()` surfaces near-misses per req, which is the actual use case. If
a skipped person later deserves a real record, the operator captures them
properly.

---

## ADR-010 — Session state extends the existing SourcingSession

**Status:** accepted · TASK-0057

**Context.** Increment 3 needs pause/resume, per-session capture attribution,
skip tracking, and counts. `SourcingSession` shipped in Increment 1 as a
boundary record with five fields.

**Decision.** Extend it in place — `pausedAt`, `resumedAt`, `pauseCount`,
`capturedCandidateIds`, `skipped`, `briefVersion` — all optional, so sessions
written by Increment 1 keep loading. `SessionStatus` gains `"paused"`.

**Why pause exists at all.** Sourcing is interrupted constantly. Without pause an
operator either leaves a session open — making its duration meaningless — or
ends it and starts another, fragmenting one search into several. Both corrupt
the counts the record exists to keep. A paused session is still *active*: what a
pause suspends is capture, not existence.

**`briefVersion` on the session.** Counts are only interpretable against the bar
that was in force. A session that reviewed 40 people against brief v2 is not
comparable to one run after the must-haves changed.

**Consequences.** `SourcingSession` grew from 8 fields to 14. Accepted: it is the
record of what a human did, and each field answers a question an operator
actually asks.

---

## ADR-009 — Unsaved-changes is tracked by revision, not timestamp

**Status:** accepted · TASK-0056

**Context.** The draft indicator first compared `lastSavedAt` against
`updatedAt`. Both are ISO timestamps with millisecond resolution, so an edit
made in the same millisecond as a save compared equal and the surface reported
"all changes saved" over a pending edit. A component test caught it.

**Decision.** `Req` carries a monotonic `rev`, incremented by every
`updateReq`, and `savedRev`, stamped by `markSaved`. `hasUnsavedChanges` is
`rev !== savedRev`.

**Rationale.** Whether a recruiter's work is persisted must not depend on clock
resolution. Widening the comparison to `<=` would have inverted the bug —
reporting unsaved forever after a save. Because both fields are persisted, an
unsaved draft is still detectable after a reload, which an in-memory dirty flag
would not survive.

**Consequences.** Two small optional fields on `Req`. `lastSavedAt` is retained
for display ("saved 12s ago") but no longer decides correctness.

---

## ADR-008 — Readiness is derived, and separate from completeness

**Status:** accepted · TASK-0056

**Context.** Increment 2 needs a completeness indicator. The obvious approach is
a stored percentage updated on save, and a single number covering "how done is
this".

**Decision.** `lib/readiness.ts` computes everything on read from the existing
`Req` and `SourcingBrief`. It stores nothing and owns no entity. It reports two
distinct values:

- **completeness** (0–100%) — how much has been authored. A progress signal.
  Nothing blocks on it.
- **readiness** (boolean) — can this req discriminate between candidates? A hard
  gate on opening for sourcing.

Within a section, the items that *block* sourcing are tracked separately from
everything merely missing. A section can be essential without every field in it
being essential: `requirements` gates sourcing because a req with no must-have
filters nobody out, but a missing nice-to-have only costs ranking.

### The approved blocker specification

These six items — and only these — prevent a req being opened for sourcing.
Approved by the product owner 2026-08-13. This list is the specification;
`canOpenForSourcing` implements it. Changing it is a product decision, not a
refactor.

| # | Blocker | Why it gates |
|---|---|---|
| 1 | Requisition code | The req cannot be identified without one |
| 2 | Role title | Nothing to source *for* |
| 3 | Owning team | No accountable owner for the search |
| 4 | Primary location | The search has no geographic anchor |
| 5 | Search headline | No stated definition of who this search is for |
| 6 | At least one must-have | **The load-bearing one.** With no must-have the req filters nobody out, so every candidate trivially matches — worse than having no req at all |

**Explicitly advisory, NOT blocking** (each appears in `suggestions`, never in
`blockers`):

- **Target locations** — approved as advisory 2026-08-13. A recruiter may
  legitimately open a remote or location-agnostic search, and blocking on it
  would force a meaningless value to be entered.
- Nice-to-haves — cost ranking quality, not the ability to source.
- Job description, intake notes, hiring manager, target/excluded industries and
  companies, keywords, experience guidance, sourcing goals.

The distinction throughout: **a blocker is something whose absence makes
sourcing produce a wrong result. Everything else makes it produce a
less-good one.**

**Rationale.** A stored percentage is a third source of truth that can disagree
with the two records it summarises — it goes stale the moment a brief is edited
through any path that forgets to recompute it.

Keeping the two values apart matters more. A req can be 90% complete and unable
to source, or 60% complete and ready. Collapsing them hides exactly the case
that matters. The first implementation derived blockers from section progress,
which silently made every optional field mandatory — a nice-to-have became
required to open a req. Tests caught it; the split above is the fix.

**Consequences.** Readiness is recomputed on every render. It is pure arithmetic
over two small objects, so this is cheaper than the staleness bugs a cached
value would cause.

---

## ADR-007 — Increment 2 extends the existing models; no authoring entity

**Status:** accepted · TASK-0056

**Context.** Authoring adds ~10 fields: full job description, intake notes,
target and excluded industries, experience guidance, sourcing goals, draft
state. A separate `ReqDraft` entity — or an authoring-shaped DTO — would have
kept the "clean" foundation models untouched.

**Decision.** Extend `Req` and `SourcingBrief` in place. Every new field is
optional, so requisitions written by Increment 1 keep loading unchanged. No new
collection was added to `WorkspaceState`; a test asserts the five collections
are unchanged.

Field placement follows ownership rather than convenience:

| Field | Home | Why |
|---|---|---|
| Job description, intake notes, sourcing goals | `Req` | Facts about the *role and the search*, true regardless of how it is searched |
| Target/excluded industries, experience guidance | `SourcingBrief` | Search *targeting*, revised as the search is tuned |
| Must-haves / nice-to-haves | `SourcingBrief.requirements` | Already existed as `required` / `preferred` |

**Alternatives rejected.** A separate draft entity means two records that can
disagree about the same requisition, plus a promotion step where they are
reconciled — the exact fragmentation ADR-002 exists to prevent, applied to reqs
instead of people.

**Consequences.** `Req` and `SourcingBrief` are larger. Accepted: they are the
domain, and the alternative was a synchronisation problem. `Candidate` and
`ReqCandidate` are untouched by this increment, and a test asserts authoring
never writes to them.

---

## ADR-001 — Build inside MondayOS as `projects/sourcingbot/`, mirroring Cue

**Status:** accepted · TASK-0054

**Context.** TASK-0053's generated design output (RES-0106, RES-0107) specified a
separate GitHub repository, PostgreSQL or MongoDB persistence, and a
`products/sourcingbot/` tree importing `@mondayos/core/product`,
`ProductTier`, and `FeatureGate`.

None of those packages exist in this repository. MondayOS has no TypeScript core
library; its one managed product, Cue App, lives at `projects/cue-app/` as a
self-contained Vite app registered in `config/projects.json`. TASK-0054 also
specifies local/mock persistence, contradicting the database recommendation, and
"managed MondayOS product", contradicting the separate repo.

**Decision.** Follow TASK-0054 and the actual Cue convention. Treat RES-0106/0107
as advisory rather than literal. Confirmed with the product owner before any
file was written.

**Consequences.** A MondayOS engineer finds identical tooling across products.
Building `@mondayos/core` first would have been a far larger increment than the
task describes. The cost is that the generated design output and the shipped
architecture diverge — this ADR is the reconciliation record.

---

## ADR-002 — Candidate and ReqCandidate are separate, and the separation is enforced in code

**Status:** accepted · TASK-0054 · the product's defining constraint

**Context.** The obvious model keys a candidate to a requisition. It is simpler
and it is what most systems do — and it fragments one human into N records,
destroying cross-req history and making concentration analytics wrong.

**Decision.** `Candidate` is a persistent person; `ReqCandidate` is one
evaluation holding `candidateId` + `reqId` and nothing that duplicates the
person. Enforce it with `assertNoIdentityDuplication`, which throws if a
Candidate-owned field appears on a ReqCandidate, plus a test asserting the
serialized store contains no person's name inside `reqCandidates`.

**Alternatives rejected.** *Single collapsed record* — the failure mode above.
*Denormalize name onto ReqCandidate for render performance* — the exact erosion
the guard prevents; `joinPipeline` handles it at render time instead.

**Consequences.** Every pipeline surface must join. Worth it: the join is one
tested function, and the model rule survives serialization, refactors, and future
contributors who have not read this file.

---

## ADR-003 — Briefs are versioned; evaluations cite the version they were made against

**Status:** accepted · TASK-0054

**Context.** A recruiter raises the bar mid-search. Existing fit scores were
computed against the old bar. Silently comparing them to the new brief makes
stale numbers look current.

**Decision.** All brief mutations route through `reviseBrief`, which bumps
`version`. `ReqCandidate.briefVersion` records what an evaluation was made
against; `needsReassessment()` flags the gap and the UI surfaces it.

**Consequences.** Evaluations are never silently invalidated *or* silently
trusted — the discrepancy is shown and a human decides. Cost: every mutation must
go through `reviseBrief`, which is why no setter mutates requirements directly.

---

## ADR-004 — The supervision gate ships before the LinkedIn workflow

**Status:** accepted · TASK-0052, TASK-0053, TASK-0054

**Context.** The LinkedIn workflow is deferred, so shipping nothing LinkedIn-
related now was an option.

**Decision.** Ship `lib/linkedin.ts` — the boundary — in this increment.
`startSession` throws without a named operator, a per-session acknowledgement,
and an open req. `PROHIBITED_CAPABILITIES` enumerates what the product refuses to
do, with `supportsCapability()` returning false for each.

**Rationale.** A boundary written after the feature it constrains is negotiated
against delivery pressure. Written first, it is the thing the feature must fit.

**Consequences.** Code exists that nothing calls in production yet. Accepted
deliberately: it is 18 tests of executable policy.

---

## ADR-005 — Install an in-memory `localStorage` for tests

**Status:** accepted · TASK-0054

**Context.** Node 22+ defines its own `localStorage` global that stays
`undefined` without `--localstorage-file`. Under vitest's jsdom environment
`window === globalThis`, so that built-in shadows jsdom's implementation — both
`localStorage` and `window.localStorage` read as undefined. Cue never noticed
because its tests never touch storage; its store simply degrades to in-memory via
a `typeof localStorage === "undefined"` guard, leaving persistence untested.

**Decision.** Add `src/test/setup.ts` with a real in-memory `Storage`, wired via
`setupFiles` in `vite.config.ts`.

**Alternatives rejected.** *Mirror Cue and leave persistence untested* — hides a
whole layer. *Run with `--localstorage-file`* — writes real files during tests
and couples the suite to a Node flag.

**Consequences.** One deliberate config difference from Cue, and the load/persist
round trip is genuinely covered.

---

## ADR-006 — An unmet required requirement caps the fit score at 0

**Status:** accepted · TASK-0054

**Context.** A weighted average across all requirements lets strong preferred
matches mask a missing required one, producing a high score for a disqualified
candidate.

**Decision.** Any required requirement not explicitly met returns 0. `unknown`
counts as unmet for required items — absence of evidence is not evidence — but is
excluded from the preferred pool rather than penalized, so "not asked yet" does
not read as a negative finding.

**Consequences.** Scores are blunt at the low end: a candidate missing one
required item scores 0 regardless of other strengths. That is the intended
signal — the rationale field carries the nuance.
