# Growth Bot

**Version:** 0.1.0  
**Status:** Draft  
**Last Updated:** 2026-08-26  
**Owner:** Lead Software Engineering

---

> **Status note.** Increments 1-7 are implemented: the Growth Workspace and its isolation
> boundary; the Content Item with its lifecycle and approval fingerprinting; the deterministic
> publishing connector layer with pause controls, retries, idempotency and an audit trail; Campaigns,
> the Content Library and growth onboarding; and the deterministic analytics layer -
> performance events, metric formulas, aggregation, time series, trends, funnels and snapshots;
> the Growth Brain, which reasons over those measurements by explicit rules and calls no model;
> and content generation - model-backed drafting through the MondayOS provider abstraction,
> the weekly marketing package and the approval inbox.
>
> **Publishing runs against a deterministic fake connector only.** There is no OAuth, no real
> platform adapter, and no browser automation. Onboarding records account *labels*, never
> credentials, and no project can be marked ready for real publishing.
>
> Increments 8-11 are **not** built: no calendar, no reports, no comment handling and no
> Growth UI. Sections
> describing those are the intended contract rather than current behaviour. See
> [Delivery Increments](#delivery-increments).

## Purpose

The Growth Bot is a MondayOS service that plans, drafts, reviews, schedules, publishes, and measures marketing activity on behalf of a MondayOS project. It is one service used by many projects, not a per-project product: sourcingBOT, Cue, and every project added later share the same engine and share none of their data.

It operates like a senior marketing manager rather than a content generator. A senior marketer does not produce seven posts because it is Sunday; they decide what the business needs to say next, say it, measure whether it worked, and change their mind on the evidence. Content is the output of that loop, not the point of it. The reasoning half of the loop is the [Growth Brain](#the-growth-brain), and it is the part that makes this service worth building — an engine that only fills a calendar is a worse version of a scheduling tool that already exists.

Three properties are non-negotiable and everything else in this document follows from them:

| Property | Meaning |
|---|---|
| **Project-isolated** | No asset, credential, draft, audience, or metric crosses a project boundary. |
| **Approval-driven** | The bot publishes exactly what a human approved, and nothing else, ever. |
| **Accountable** | Every published item traces back to an objective, an approval, and a measured outcome. |

---

## What the Growth Bot Does Not Own

Explicit boundaries, so the service does not absorb the rest of MondayOS:

- **Agent execution.** Roles, providers, and run records belong to the Multi-Agent Runtime ([AGENTS.md](AGENTS.md)). The Growth Bot requests work from roles; it does not call providers directly.
- **The approval decision.** The gate is `agents/gates.py::ApprovalGate` ([APPROVAL_GATES.md](APPROVAL_GATES.md)). The Growth Bot declares intent and respects the verdict; it does not implement a second approval system. See ADR-012.
- **Learned knowledge.** Findings the Growth Brain produces are knowledge entries in `knowledge/` ([KNOWLEDGE_SYSTEM.md](KNOWLEDGE_SYSTEM.md)), not a private memory.
- **Credentials.** Platform tokens live in the secret store, never in a workspace file. See ADR-011.
- **Task tracking.** Marketing work that needs engineering becomes a task in `tasks/`, like any other work.

---

## Vocabulary

Defined once here and used consistently throughout the service.

| Term | Definition |
|---|---|
| **Growth Workspace** | The complete, project-scoped marketing state for exactly one project. The unit of isolation. |
| **Content Item** | One publishable artifact bound to one platform, one account, one scheduled time. The unit of approval. |
| **Weekly Package** | A proposed objective, theme, audience, and set of Content Items for one week. The unit of planning. |
| **Approval Fingerprint** | The hash of the fields a human actually approved. Its change is the definition of a material change. See ADR-013. |
| **Growth Brain** | The reasoning layer that decides what to say next and explains why last week worked. See ADR-014. |
| **Campaign** | A named, time-bounded set of Content Items sharing one objective and one success metric. |
| **Experiment** | A hypothesis with a success metric, tested by varying one attribute of otherwise comparable Content Items. |

---

## Project Isolation

Every project owns exactly one Growth Workspace, and a workspace is the boundary the service is designed around rather than a filter applied to shared data. The distinction matters: a filter is a bug away from leaking, and the failure mode here is publishing one client's message to another client's audience — unrecoverable, publicly visible, and the kind of error that ends the product's credibility in a single afternoon. Reasoning about a project loads that project's workspace and cannot address another. ADR-011 records the decision and its costs.

### What a workspace contains

| Section | Contents |
|---|---|
| **Business** | Name, description, website, industry, products, services, pricing, competitors |
| **Brand** | Voice, tone, style rules, colors, fonts, logos, design system, approved imagery |
| **Audience** | ICPs, personas, industries, job titles, company size, geography, interests, pain points |
| **Marketing** | Objectives, KPIs, campaigns, funnels, content pillars, CTAs |
| **Publishing** | Per-platform account bindings — LinkedIn, X, Instagram, Facebook, TikTok, YouTube, Threads |
| **Analytics** | Reach, impressions, CTR, followers, conversions, leads, revenue, CAC, ROAS, per-campaign performance |
| **Library** | Every Content Item, in every lifecycle state, with its approval and measurement history |

### Credentials are referenced, never stored

A workspace stores a **binding** — platform, account handle, account ID, and the name of a secret — and never the token itself. This is the one place the workspace deviates from [ADR-005](DECISIONS.md) (Git as the source of truth for persistent state), because a Git-tracked, human-readable OAuth token is a credential leak with version history. The binding is diffable and reviewable; the secret it names resolves at publish time and appears in no file, no log, and no agent prompt.

### The portfolio view

MondayOS presents portfolio-level growth reporting — comparison, weekly trends, campaign health, publishing status — without breaching isolation. It does this by reading **pre-computed per-project aggregates**, never project workspaces. An aggregate carries counts, rates, and deltas; it carries no copy, no media, no audience definition, and no account binding. A portfolio reader therefore learns that project A converts better than project B and cannot learn what project A said.

---

## The Growth Brain

The weekly package answers *what are we posting*. The Growth Brain answers *why that, and what did we learn* — and without it the service is a scheduler with a language model attached.

The Brain runs continuously rather than on Sunday, because the inputs it depends on do not arrive weekly. It maintains standing answers to six questions and revises each one as evidence lands:

| Question | Primary evidence |
|---|---|
| What should we talk about next? | Content gaps, pillar coverage, funnel stage with the weakest supply |
| Why did last week's content perform well or poorly? | Per-item metrics against the objective it was published for |
| What are competitors discussing? | Competitor monitoring via the `research` role |
| What is trending in our industry? | Trend signals via the `research` role, filtered to this workspace's audience |
| What is missing from our library? | Library coverage against pillars, personas, and funnel stages |
| What should we build next to drive leads? | Conversion attribution — which content produced qualified demand |

Two rules keep the Brain honest.

**Every recommendation carries its evidence.** A recommendation with no evidence is an opinion, and an opinion generated by a model at scale is noise that costs a human time to evaluate. A recommendation states the observation, the inference, the confidence, and what would falsify it.

**A performance explanation is a hypothesis until an experiment confirms it.** Post-hoc reasoning about why a post performed well is the single easiest place for this service to manufacture confident nonsense — sample sizes are small, platform distribution is opaque, and any narrative fits. The Brain therefore labels explanations as hypotheses and promotes one to a finding only after an [experiment](#experiment-engine) tests it. Confirmed findings become knowledge entries; unconfirmed ones expire.

ADR-014 records why the Brain is a layer inside this service rather than a sibling bot.

---

## Weekly Planning Workflow

Each Sunday the Growth Bot assembles a **Weekly Package** for a project and submits it for review. The package is a proposal in full: a human reads it once and either approves it, edits it, or rejects it.

A package contains:

- **Objective** — a measurable business outcome, e.g. *generate 50 demo requests*
- **Theme** — the week's narrative spine, e.g. *AI Recruiting Week*
- **Audience** — which ICPs and personas the week targets
- **Rationale** — the Growth Brain's reasoning for this objective and theme, with evidence
- **Calendar** — the proposed Content Items, each complete enough to approve

Every Content Item in the calendar specifies platform, account, copy, media, CTA, destination URL, campaign, expected goal, expected audience, publish time, and any warnings raised during review. An item missing any of these is not reviewable, and an unreviewable item cannot be approved.

A representative week spans formats rather than repeating one: a carousel, a long-form piece with a newsletter and a snippet cut from it, a video, an educational post, a customer story, a behind-the-scenes item, and a weekly summary. The mix is a default, not a rule — the Brain proposes the mix the objective calls for, and a week that needs four posts proposes four.

---

## Content Lifecycle

Every Content Item moves through one state machine. State is explicit, stored, and never inferred.

```
Draft ──▶ AI Review ──▶ Ready for Review ──▶ Approved ──▶ Scheduled
                             │  ▲                                │
             Changes Requested  │                                ▼
                             └──┘                          Publishing
                                                                 │
                          ┌──────────────────┬───────────────────┤
                          ▼                  ▼                   ▼
                       Retry          Manual Review          Published
                          │                  │                   │
                          └──────▶ Cancelled ◀──┘                ▼
                                                             Measured
                                                                 │
                                                                 ▼
                                                             Archived
```

| State | Meaning |
|---|---|
| **Draft** | Being written. No review has run. |
| **AI Review** | Automated checks: brand compliance, claim verification, link validity, platform constraints. |
| **Ready for Review** | Awaiting a human. Carries every warning AI Review raised. |
| **Changes Requested** | A human returned it with notes. Returns to Draft on edit. |
| **Approved** | A human approved a specific fingerprint. See ADR-013. |
| **Scheduled** | Handed to the publishing connector with a fire time. |
| **Publishing** | In flight at the platform. |
| **Published** | Live, with a platform post ID recorded. |
| **Measured** | Metrics collected and attributed to the objective it was published for. *(Increment 4 — not implemented.)* |
| **Archived** | Terminal. Retained as evidence for the Growth Brain. *(Increment 4 — not implemented.)* |
| **Failed** | A publish attempt did not succeed. A transient failure carries a backoff window and can be retried under the same approval; a permanent one requires a human. |
| **Cancelled** | Terminal. Will not publish. |

Cancellation is allowed from every state up to and including Scheduled, and from Failed. It is
refused once an item is Publishing — the request is already with the platform and MondayOS cannot
claim it did not land — and once Published, where withdrawal is a deletion rather than a
cancellation and carries different authority.

Failure handling distinguishes the two cases deliberately. A transient failure — a rate limit, a timeout — retries under the existing approval, because nothing about the approved content changed. A non-transient failure — a rejected token, a policy refusal, a dead destination URL — routes to Manual Review, because something about the world changed and the approval may no longer mean what it meant.

---

## Approval Model

Nothing publishes automatically. This is a structural property, not a configuration setting: the service has no code path from Draft to Published that does not pass a recorded human approval.

### Enforcement reuses the existing gate

`publish_content` is a gated action in the Multi-Agent Runtime, alongside `commit`, `push`, `secrets`, `live_trade`, and `destructive`. A Growth Bot run that declares an intent to publish is blocked unless a human approval is present, by exactly the mechanism described in [APPROVAL_GATES.md](APPROVAL_GATES.md). The Growth Bot adds no second approval system — ADR-012 explains why a parallel one would be a security regression rather than a feature.

### What a human approves

An approval binds seven fields together: **project, platform, account, media, copy and CTA, destination URL, and scheduled date and time**. Approval is of that combination, not of the item in general. Approving a post for LinkedIn is not approval to publish it to X; approving it for Tuesday 9am is not approval to publish it Friday.

### What resets an approval

The specification rule is *if anything changes, approval resets*. Implemented literally that is either unenforceable or unusable, so the service makes it precise with an **Approval Fingerprint**: the hash of the seven approved fields. Changing any field changes the fingerprint, and an item whose current fingerprint differs from its approved fingerprint is not approved — it returns to Ready for Review automatically. Fields outside the fingerprint, such as an internal note or a tag, change freely without disturbing the approval. ADR-013 records the decision and the exact field list.

The property this buys is worth stating plainly: an approved-and-then-edited item cannot publish, and no code needs to remember to check.

---

## Content Engine

The Growth Bot drafts the formats a marketing function actually ships:

**Social** — LinkedIn posts, carousels, Instagram posts, Threads, X posts, short-form video scripts.  
**Long-form** — blogs, SEO articles, newsletters, customer stories, landing pages.  
**Campaign** — product launches, educational series, email sequences, webinars, conference campaigns, recruiting campaigns.  
**Announcements** — release notes, press announcements, partnership announcements.

Every draft is generated against the workspace's brand section, not against a general sense of good writing. Voice, tone, style rules, approved imagery, and content pillars are inputs to generation and criteria in AI Review, which is what makes brand compliance measurable rather than aspirational.

---

## Collaboration With Other Roles

The original specification describes eight sibling bots — Research, Design, Video, Analytics, Brand, Website, Publishing, Risk. MondayOS already has the abstraction those describe: work routes to a **role**, and a role resolves to an agent bound to a provider ([AGENT_ROLES.md](AGENT_ROLES.md)). Adding a role is one entry in `agents/roles.py::ROLES`. The Growth Bot therefore collaborates with roles rather than with services, and adds no new runtime.

| Specification bot | Realization |
|---|---|
| Research Bot | Existing `research` role |
| Risk Bot | Existing `security` role, for claim and compliance review |
| Brand Bot | New `brand` role — voice, tone, and design-system compliance |
| Design Bot | New `design` role — imagery, carousels, layout |
| Analytics Bot | New `analytics` role — measurement, attribution, experiment readout |
| Video Bot | Deferred. A `design` role capability until video volume justifies separation. |
| Website Bot | Out of scope. Site changes are engineering tasks in `tasks/`. |
| Publishing Bot | **Not a role.** Publishing is an integration connector, not a reasoner. |

That last row is the load-bearing one. Publishing takes an approved fingerprint and calls a platform API — it makes no judgements, so modeling it as an agent would put a language model on the one code path that must be deterministic. It is a connector, and it refuses any item whose fingerprint does not match.

A representative flow, end to end:

```
research role finds a trend
      ↓
Growth Brain decides it fits this week's objective   ← reasoning, with evidence
      ↓
design role produces the carousel
      ↓
Growth Bot writes the copy against workspace brand
      ↓
security + brand roles review claims and compliance   ← AI Review
      ↓
HUMAN APPROVES  → fingerprint recorded                ← the only path forward
      ↓
publishing connector schedules and publishes          ← deterministic
      ↓
analytics role measures against the objective
      ↓
Growth Brain proposes the next experiment
```

---

## Comment Assistant

Comment handling starts in **monitor-only** mode for every project, with no exceptions and no per-project override at launch.

The bot ingests comments, drafts replies, and routes them for approval. It publishes no reply automatically. Certain categories never reach an automated path at all and escalate directly to a human: legal, billing, threats, media enquiries, privacy, security, partnerships, and complaints.

Automated replies unlock for a project only after that project has an approved **Response Library** — a set of vetted responses with the conditions under which each applies. The reasoning is the same asymmetry that governs publishing: a good automated reply saves a few minutes, and a bad one is a public statement made by a machine on the company's behalf. Until a human has decided what the machine is allowed to say, it says nothing.

---

## Analytics

### Per project

Content published, publishing success rate, engagement, reach, clicks, conversions, follower growth, top and worst posts, best and worst platform, experiment results, and the Growth Brain's current recommendations.

Every metric attributes back to the objective the item was published for. Engagement that serves no objective is reported as such rather than presented as success — a post with high reach and no contribution to the week's goal is a finding, not a win.

### Portfolio

Portfolio growth, project comparison, weekly trends, campaign health, conversion comparison, and publishing status — computed from per-project aggregates only, as described in [The portfolio view](#the-portfolio-view).

---

## Experiment Engine

The Growth Bot proposes experiments continuously, because the Growth Brain's performance explanations are hypotheses until something tests them.

An experiment is well-formed only if it states all five of:

| Field | Requirement |
|---|---|
| **Hypothesis** | A specific, falsifiable claim |
| **Variable** | The single attribute that varies — timing, format, hashtag count, caption length, CTA, thumbnail, audience |
| **Success metric** | Chosen before the experiment runs |
| **Expected outcome** | Direction and rough magnitude, stated in advance |
| **Confidence** | The Brain's prior, so systematic overconfidence becomes visible |

Experiments require approval like any other content, because an experiment publishes real content to a real audience. Stating the expected outcome and the metric in advance is what prevents the common failure of reading a result after the fact and declaring whichever number moved to be the one that mattered.

---

## Emergency Controls

Controls exist at five scopes, each one able to halt publishing without unwinding state:

| Control | Scope |
|---|---|
| Pause Post | One Content Item |
| Pause Campaign | Every item in one campaign |
| Pause Platform | Every item bound to one platform, across a project |
| Pause Project | Every item in one Growth Workspace |
| Pause Portfolio | Every item in every workspace |
| Emergency Stop | Portfolio-wide halt, requiring an explicit human resume |

Pausing is non-destructive: items hold their state and their approvals, and a scheduled item whose fire time passes while paused enters Manual Review rather than publishing late. Publishing late is the wrong default — a launch announcement that fires after the launch, or a comment on a news event that fires after the event, is worse than one that never fires.

A pause takes effect at the publishing connector, which is the single choke point every item passes through. Nothing publishes while a pause covering it is active, regardless of what any other component believes.

---

## Storage Layout

Runtime state follows the pattern established by `tasks/`, `agents/`, and `logs/` — Markdown with YAML frontmatter under the project root, per [ADR-003](DECISIONS.md).

```
growth/                              ← service code (not yet implemented)
└── workspaces/
    └── <project-slug>/              ← one Growth Workspace; the isolation boundary
        ├── workspace.md             business, brand, audience, marketing
        ├── bindings.md              platform + account + secret NAME (never the secret)
        ├── content/CONTENT-NNNN.md  one Content Item, with lifecycle + fingerprint
        ├── packages/WEEK-YYYY-WW.md one Weekly Package
        ├── experiments/EXP-NNNN.md  one experiment
        └── aggregates.json          ← the only file the portfolio view may read
```

The service package under `growth/` does not exist yet. It gains a `README.md` meeting the module standard in [DOCUMENTATION_STANDARDS.md](DOCUMENTATION_STANDARDS.md) when the first code lands, and not before — a README describing a module that does not exist is misinformation.

---

## Future Integrations

Deferred, in rough dependency order. None is required for the first increment.

**Publishing** — LinkedIn, Meta Business Suite, YouTube Studio, Buffer, Hootsuite.  
**Measurement** — Google Analytics, Google Search Console.  
**CRM and revenue** — HubSpot, Salesforce, Stripe.  
**Email** — Mailchimp, ConvertKit, Beehiiv.  
**Web and commerce** — WordPress, Webflow, Shopify.  
**Automation and notification** — Zapier, n8n, Slack, Discord.

Each integration is a connector behind a common interface, for the same reason [ADR-004](DECISIONS.md) puts model providers behind one: platform APIs change on their owners' schedule, and a change should touch one adapter rather than the service.

---

## Success Metrics

The Growth Bot is judged on business outcomes and on trustworthiness, and the second set is what distinguishes it from a content generator.

| Dimension | Measured by |
|---|---|
| Content quality | Approval rate on first submission |
| Publishing reliability | Publish success rate; time from Approved to Published |
| Campaign consistency | Weekly packages delivered and approved on schedule |
| Conversion improvement | Conversion rate trend, attributed to objectives |
| Audience growth | Follower and reach trend per platform |
| Experiment success | Share of hypotheses confirmed; calibration of stated confidence |
| Time saved | Human minutes per published item |
| Brand compliance | AI Review pass rate; brand violations reaching a human |
| **Isolation integrity** | Cross-workspace access attempts — the target is zero, and any non-zero value is an incident |
| **Approval integrity** | Items published without a matching fingerprint — the target is zero, and any non-zero value is an incident |

---

## Delivery Increments

Each increment leaves the service useful on its own. No increment ships a publishing path before the approval path that governs it.

**Increment 1 — Workspace and isolation.** Growth Workspace schema, project-scoped loading, credential bindings, and the isolation tests. No content, no publishing. Delivers a reviewable brand and audience definition per project.

**Increment 2 — Content and approval.** Content Item schema, the lifecycle state machine, approval fingerprinting, and `publish_content` registered as a gated action. Items reach Approved and stop there. Nothing can publish, which makes this increment safe to ship.

**Increment 3 — Publishing.** *(Implemented, fake connector only.)* The provider-neutral connector interface, the deterministic dispatcher and its ten-gate sequence, bounded retries with backoff and jitter, clock-free idempotency, the pause scopes, and the audit trail. Real platform adapters and the credential framework they need are deliberately deferred: MondayOS has no OAuth layer, and inventing a second credential system for social publishing was out of scope.

**Increment 4 — Campaigns, library, onboarding.** *(Implemented.)* Campaign as the planning object with its own lifecycle, a query-layer Content Library over existing storage, per-platform variants as separate items sharing a `variant_group_id`, and onboarding that marks a project planning-ready but never real-publishing-ready.

**Increment 5 — Measurement.** *(Implemented, synthetic/imported data only.)* Performance events with explicit provenance, metric formulas as pure functions, content/campaign/platform/workspace aggregation, time series, trends, conversion funnels, snapshots, and the per-project aggregate the portfolio view will read. No platform adapter exists, so `source=platform` is refused and every metric derived from synthetic or imported data is labelled as such.

**Increment 6 — Growth Brain and experiments.** *(Implemented, fully deterministic.)* Four record kinds that are never conflated, ten opportunity detectors, evidence-and-falsifier-enforced recommendations, project-scoped marketing memory, experiments that refuse to name a winner on a thin sample, linear-run-rate forecasting, and reproducible health scores. No model is called. ADR-014 is now Accepted.

**Increment 5 (original numbering) — Growth Brain and experiments.** The six standing questions, evidence-carrying recommendations, the experiment engine, and promotion of confirmed findings to knowledge entries. Requires increment 4, because the Brain reasons over measured outcomes and has nothing to reason about before then.

**Increment 6 — Portfolio and comment assistant.** Portfolio views over aggregates; monitor-only comment handling with escalation.

---

## Open Questions

Recorded rather than guessed. Each needs a decision before the increment that depends on it.

1. **Approval delegation.** May a project owner delegate approval, and does the service need approver roles distinct from MondayOS users? Blocks increment 2.
2. **Content Item scope for multi-platform posts.** One item per platform is the current model. A single logical post spanning five platforms then needs five approvals, which is correct for safety and tedious in practice. Blocks increment 2.
3. **Metric attribution window.** How long after publication does a conversion still attribute to a Content Item? The number materially changes every reported conversion figure. Blocks increment 4.
4. **Experiment sample sufficiency.** What minimum volume makes an experiment readable? Organic social rarely reaches statistical significance, so the service needs a defensible standard rather than a p-value it cannot honestly compute. Blocks increment 5.
5. **Competitor monitoring sourcing.** Which sources, at what cadence, and within whose terms of service. Blocks increment 5.
