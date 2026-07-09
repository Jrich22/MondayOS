#!/usr/bin/env python3
"""
Cue App Product Workspace — MondayOS.

Populates MondayOS with the complete product-management structure for Cue App
*entirely through the Monday public API*, so MondayOS is the single system of
record. Nothing here is hand-authored prose that could instead live as
structured knowledge or tasks.

Cue App is an AI-native event-operations platform for VC firms — portfolio
events, executive programs, founder dinners, investor days, demo days, and
community events. It aims to compete with modern event platforms while
differentiating through AI-native planning, portfolio intelligence, and event
execution workflows.

What this script creates (all in the MondayOS knowledge + task stores, scoped to
the `cue-app` component):

  Knowledge (Monday.learn):
    - Project Overview            (what Cue App is / current state)
    - Project Charter             (vision, goals, constraints, metrics)
    - Product Vision              (the north star and the wedge)
    - Target Users                (personas and jobs-to-be-done)
    - Competitive Positioning     (landscape + differentiation)
    - MVP Scope                   (in / explicitly out)
    - V1 Roadmap                  (milestones and release order)
    - Risk Register               (product, technical, market, operational)
    - Definition of Done
    - Scope Guardrails            (decision: what we are NOT building yet)
    - Engineering Backlog         (epic map with real task IDs)
    - Sprint 1 Plan               (goal, selected work with real task IDs)

  Tasks (Monday.task):
    - 10 epics
    - 8 Sprint 1 foundation tasks with dependencies expressed in context

  Project registry (Monday.project):
    - cue-app registered as a MondayOS-managed project

It is idempotent-guarded: if the workspace already exists it refuses to run
again unless `--force` is passed. Re-running is the supported way to rebuild the
workspace from this single structured source of truth.

Usage:
    python projects/cue-app/setup_workspace.py [--project-root PATH] [--force]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from monday import Monday, MondayConfig

_COMPONENT = "cue-app"
_OWNER = "human:cue-product-owner"
_ENG = "human:cue-eng-lead"
_SENTINEL_TITLE = "Cue App Project Charter"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _workspace_dir() -> Path:
    return _repo_root() / "projects" / "cue-app"


# ---------------------------------------------------------------------------
# Knowledge content (structured — captured via Monday.learn)
# ---------------------------------------------------------------------------

OVERVIEW = """\
# Cue App — Overview & Current State

## What it is
Cue App is an AI-powered event-operations platform purpose-built for venture
capital firms. It runs the full lifecycle of the events VC firms actually
throw — portfolio events, executive programs, founder dinners, investor days,
demo days, and community gatherings — from first planning conversation through
post-event recap.

## Why it exists
VC firms run a relentless calendar of high-stakes, relationship-driven events,
but they run them on a patchwork of generic tools (spreadsheets, calendar
invites, mail-merge RSVPs, event platforms built for conferences). None of it
understands the thing that makes VC events different: the guest list *is* the
portfolio and the network, and the point of every event is a relationship
outcome. Cue App is event operations that is AI-native and portfolio-aware by
default.

## The wedge
Three things a generic event platform cannot do well:
1. **AI-native planning** — describe the event in a sentence; Cue drafts the
   agenda, run-of-show, guest segments, and vendor checklist.
2. **Portfolio intelligence** — guests and companies are tagged to the firm's
   portfolio, so who-attended and who-should-attend is a first-class signal.
3. **Execution workflows** — run-of-show, vendor/venue tracking, and day-of
   coordination, not just invitations.

## Current state
Greenfield. This workspace is the system of record: charter, vision, users,
positioning, MVP scope, roadmap, backlog, risks, Definition of Done, and the
Sprint 1 plan all live as MondayOS knowledge and tasks. No application code
exists yet — Sprint 1 builds the MVP foundation.
"""

CHARTER = """\
# Cue App Project Charter

## Vision
Become the operating system for how venture firms plan, run, and learn from
their events — an AI-native, portfolio-aware event platform that turns event
operations into relationship intelligence.

## Goals
1. **AI-native planning** — a firm can go from a one-line brief to a credible
   draft agenda, run-of-show, and guest plan in minutes, not days.
2. **Portfolio-aware guest management** — every guest and company is tied to the
   firm's portfolio and network, so targeting and follow-up are intelligent.
3. **End-to-end execution** — invite/RSVP, vendor/venue tracking, and
   run-of-show live in one system, replacing the spreadsheet patchwork.
4. **Learning loop** — every event produces a structured recap and insights that
   make the next event better.

## Constraints (this stage)
- Serious event-operations product, but MVP-first: do not overbuild.
- No payments/ticketing yet.
- No complex third-party integrations yet (CRM, calendar, email providers).
- No production authentication yet — auth is a placeholder in the MVP.
- Single-firm mental model for now; multi-tenant hardening comes later.

## Success metrics (V1 horizon)
- A firm can create an event and a guest list end-to-end without leaving Cue.
- AI agenda draft is rated "usable with light edits" in the majority of trials.
- Time-to-plan a standard event drops meaningfully vs. the spreadsheet baseline.
- Post-event recap is generated for every completed event.
- Design-partner firms return to run their next event in Cue.
"""

VISION = """\
# Cue App — Product Vision

## North star
Every event a venture firm runs should make the firm smarter about its portfolio
and network. Cue is the system that plans the event, runs it, and closes the
loop into relationship intelligence.

## Why now
- Firms run more founder/portfolio/community events than ever, and events are a
  primary top-of-funnel and portfolio-support channel.
- AI can now do the tedious first-draft planning work (agendas, run-of-show,
  segments, checklists) that today eats an ops person's week.
- Generic event tools treat a guest as a row; a VC firm treats a guest as a
  founder, an LP, a co-investor, or a portfolio exec. That context is the moat.

## The differentiated bet
Cue is not "another event platform." It is **AI-native** (planning is generated,
not just stored), **portfolio-aware** (the guest graph is tied to the firm's
companies and network), and **execution-first** (run-of-show and vendor/venue
tracking, not just invitations). A conference-ticketing tool optimizes check-in
throughput; Cue optimizes relationship outcomes.

## Three-horizon shape
- **Horizon 1 (MVP → V1):** event workspace, guest management, AI agenda draft,
  invite/RSVP, vendor/venue tracking, run-of-show, portfolio tagging, recap.
- **Horizon 2:** integrations (calendar/email/CRM), richer portfolio graph,
  cross-event analytics, collaborative planning for ops teams.
- **Horizon 3:** an AI event operator that proactively proposes events, guest
  lists, and follow-ups from portfolio signals.
"""

TARGET_USERS = """\
# Cue App — Target Users

Primary buyer and users sit inside venture capital firms. Cue is designed for
the people who actually make firm events happen and the partners who care about
their outcomes.

## Primary persona — Platform / Events Lead ("the operator")
Runs the firm's event calendar end-to-end. Today lives in spreadsheets, calendar
invites, and RSVP mail-merges.
- **Jobs:** plan an event fast, manage the guest list, coordinate vendors/venue,
  run the day-of show, report on outcomes.
- **Pain:** every event rebuilt from scratch; no memory of who came last time;
  no portfolio context on the guest list; day-of is chaos.
- **Cue value:** AI drafts the plan; guest list is portfolio-aware; run-of-show
  and vendor tracking are built in; recap is automatic.

## Secondary persona — Partner / Investor (the stakeholder)
Hosts or headlines events (founder dinners, investor days, demo days).
- **Jobs:** know who is coming and why they matter, show up prepared, follow up.
- **Cue value:** portfolio-aware guest view and a clean run-of-show.

## Secondary persona — Portfolio Success / Community Lead
Runs recurring community and portfolio-exec programming.
- **Jobs:** run programs at cadence, track engagement across events, segment
  the community.
- **Cue value:** reusable event workspaces, portfolio tagging, cross-event recap.

## Event types Cue must serve
Portfolio events · executive programs · founder dinners · investor days ·
demo days · community events.

## Explicitly not the target (this stage)
Large public conferences, paid ticketed events, and consumer event discovery —
those are served well by existing platforms and are out of scope.
"""

POSITIONING = """\
# Cue App — Competitive Positioning

## Landscape
- **Conference/ticketing platforms** (Eventbrite, Hopin-style): optimized for
  paid, public, high-volume events and check-in throughput. Weak at
  relationship context and AI planning.
- **Invite/RSVP tools** (Luma, Partiful-style): great lightweight invites and
  RSVP, but shallow on execution (run-of-show, vendors) and blind to portfolio
  context.
- **Generic productivity** (spreadsheets, Notion, calendar, mail-merge): infinite
  flexibility, zero leverage — everything is rebuilt by hand every time.
- **CRM/community tools** (Airtable/Salesforce/community platforms): store
  contacts, but are not event-operations systems.

## Where Cue wins
1. **AI-native planning** — competitors store what you type; Cue drafts the
   agenda, run-of-show, guest segments, and vendor checklist from a brief.
2. **Portfolio intelligence** — the guest list is tied to the firm's portfolio
   and network, so targeting, attendance history, and follow-up are intelligent.
3. **Execution depth** — run-of-show and vendor/venue tracking, not just an
   invite link.
4. **Purpose-built for VC** — the data model speaks founders, LPs, co-investors,
   and portfolio companies, not generic "attendees."

## Positioning statement
For venture firms that run relationship-driven events, Cue App is the AI-native
event-operations platform that plans, runs, and learns from every event —
because it understands the portfolio behind the guest list. Unlike ticketing
platforms and lightweight invite tools, Cue drafts the plan, tracks execution,
and turns each event into portfolio intelligence.

## What we will NOT try to win on (yet)
Paid ticketing, high-volume public-conference logistics, and consumer event
discovery. Cue is deliberately narrow: VC event operations, done exceptionally.
"""

MVP_SCOPE = """\
# Cue App — MVP Scope

MVP-first. The goal is a coherent, credible event-operations foundation, not a
feature-complete platform. Do not overbuild.

## In scope (MVP)
- **Event workspace** — the container for a single event and its state.
- **Event dashboard** — list of events with status at a glance.
- **Create-event flow** — go from nothing to a new event workspace.
- **Event detail page** — the working surface for one event.
- **Guest / attendee list model** — represent guests and their status.
- **AI agenda builder (first draft)** — generate a draft agenda from a brief.
- **Invite & RSVP flow (basic)** — invite guests and capture RSVP status.
- **Vendor / venue tracker (basic)** — track vendors and the venue for an event.
- **Run-of-show builder (basic)** — an ordered day-of timeline.
- **Portfolio-company tagging** — tag guests/companies to the portfolio.
- **Post-event recap (basic)** — a structured summary after an event.
- **AI assistant placeholder** — the surface where AI help will live.

## Explicitly OUT of MVP
- **Payments / ticketing** — not now.
- **Complex integrations** — no CRM, calendar, or email-provider integrations.
- **Production authentication** — auth is a placeholder, not real identity.
- **Multi-tenant hardening, roles/permissions, billing** — later.
- **Native mobile apps** — web-first.
- **Public-conference logistics** (badges, check-in hardware) — out of scope.

## MVP shipping principle
Every MVP surface should feel like a real product slice a design-partner firm
could touch, even if backed by seed data and placeholders. Depth comes in V1.
"""

ROADMAP = """\
# Cue App — V1 Roadmap

Release order follows the value chain: stand up the workspace, make the guest
list real, then layer AI planning and execution, then close the learning loop.

## Sprint 1 — MVP Foundation  (current)
App shell, auth placeholder, event dashboard, create-event flow, event detail
page, guest list model, AI assistant placeholder, seed demo data. Goal: a
walkable product skeleton with real navigation and data shape.

## M1 — Guests & Invites  → the guest list becomes real
- Full guest/attendee management (segments, status, import-by-paste).
- Basic invite & RSVP flow with status tracking.
- Portfolio-company tagging on guests and companies.

## M2 — AI Planning  → the differentiated wedge
- AI agenda builder: brief → draft agenda.
- Run-of-show builder seeded from the agenda.
- AI assistant layer wired to real planning actions (beyond placeholder).

## M3 — Execution  → run the event
- Vendor / venue tracker with checklist and status.
- Run-of-show day-of view.
- Event lifecycle states (draft → planning → live → complete).

## M4 — Learn  → close the loop
- Post-event recap and insights.
- Cross-event and portfolio-level views.
- Admin dashboard maturation.

## Priority rationale
Guests before AI: the AI planning value depends on a real event + guest model.
Execution before analytics: you must run events before you can learn from them.
Everything explicitly out of MVP scope (payments, integrations, production auth)
stays deferred until the core loop is proven with design partners.
"""

RISK_REGISTER = """\
# Cue App — Risk Register

Each risk: **likelihood × impact → mitigation**.

## Product risks
- **AI planning output is mediocre / not trusted** — High × High →
  scope AI to *draft* (human edits), measure "usable with light edits" rate,
  keep the operator in control; start with agenda before harder surfaces.
- **"Just another event platform" perception** — Med × High →
  lead every surface with the portfolio-aware + AI-native wedge; do not chase
  conference-ticketing feature parity.
- **Scope creep beyond MVP** — High × Med →
  Scope Guardrails decision is binding; payments/integrations/production auth
  are explicitly deferred and enforced in Definition of Done review.

## Technical risks
- **Data model doesn't fit portfolio semantics** — Med × High →
  model guests/companies with portfolio tags as first-class in Sprint 1 so later
  intelligence has a foundation; validate with a real firm's event.
- **AI cost/latency on planning** — Med × Med →
  start with a placeholder + single generation surface; cache drafts; do not put
  AI on every interaction.
- **Placeholder auth leaks into V1** — Med × High →
  isolate auth behind a seam now so real identity drops in without a rewrite.

## Market / GTM risks
- **Narrow ICP (VC firms) limits TAM** — Med × Med →
  accepted wedge; VC events are high-value and reference-dense; adjacency
  (accelerators, corp dev, PE) is a later expansion, not a Sprint 1 concern.
- **Design partners don't return for event #2** — Med × High →
  make the recap + reusable workspace the retention hook; measure return rate.

## Operational risks
- **No system of record for product decisions** — mitigated →
  MondayOS is the system of record; charter, scope, roadmap, and decisions are
  captured as structured knowledge and tasks.
- **Single-maintainer bus factor** — Med × Med →
  Definition of Done requires knowledge capture so rationale survives turnover.
"""

DEFINITION_OF_DONE = """\
# Cue App — Definition of Done

A unit of work is **Done** only when ALL of the following hold:

1. Code is reviewed and approved.
2. It works end-to-end via the Cue App UI (or the seeded demo path) — not just
   in isolation.
3. It stays inside the current MVP scope: no payments, no complex integrations,
   no production auth introduced without an explicit scope decision.
4. Tests cover the new behavior where there is logic to test; the suite is green.
5. Public functions/components have clear names and docstrings; no new untracked
   TODO/FIXME left behind.
6. The data shape is consistent with the portfolio-aware model (guests/companies
   carry portfolio context where relevant).
7. Knowledge is captured in MondayOS (decision/pattern/lesson) when the work
   established a non-obvious choice.
8. The associated MondayOS task is moved to its terminal state with a reason.

This Definition of Done is binding for every task in the Cue App backlog.
"""

SCOPE_GUARDRAILS = """\
# Cue App — Scope Guardrails (Decision)

**Decision:** For the MVP foundation and the immediate roadmap, Cue App will NOT
build the following, regardless of how easy or tempting they are. This is a
binding scope decision, recorded so it is enforced in Definition of Done review.

## Deferred — do NOT build yet
1. **Payments / ticketing.** No paid tickets, no checkout, no billing.
2. **Complex integrations.** No CRM, calendar, or email-provider integrations in
   the MVP. Invites/RSVP are handled in-app.
3. **Production authentication.** Auth is a *placeholder*. No real identity,
   SSO, org management, or role-based access control yet.
4. **Multi-tenant hardening & permissions.** Single-firm mental model for now.
5. **Overbuilding any surface.** Each MVP surface is a credible slice, not a
   feature-complete module. Depth is earned in V1 with design-partner signal.

## Why
Speed and focus. The unproven, differentiated bet is AI-native + portfolio-aware
event operations. Payments, integrations, and production auth are known,
solvable, and undifferentiated — they add build cost and risk without testing
the core hypothesis. We defer them until the core event loop is validated.

## How to apply
Any task that would introduce a deferred capability must first update this
decision (an explicit scope change), not slip in under another task. Reviewers
reject scope leakage under the Definition of Done.
"""


# ---------------------------------------------------------------------------
# Backlog definition (created via Monday.task)
# ---------------------------------------------------------------------------

# Epics: (key, title, priority, objective)
EPICS = [
    ("event_workspace", "[EPIC] Event Workspace", "P1",
     "The core container for a single event and its state — the surface every "
     "other capability hangs off of."),
    ("guests", "[EPIC] Guest / Attendee Management", "P1",
     "Represent, segment, and manage guests and attendees, portfolio-aware by "
     "design."),
    ("ai_agenda", "[EPIC] AI Agenda Builder", "P1",
     "Generate a credible draft agenda for an event from a short brief — the "
     "AI-native planning wedge."),
    ("rsvp", "[EPIC] Invite & RSVP Flow", "P1",
     "Invite guests and capture RSVP status in-app, without external email/CRM "
     "integrations."),
    ("vendors", "[EPIC] Vendor / Venue Tracker", "P2",
     "Track vendors and the venue for an event with status and a basic "
     "checklist."),
    ("run_of_show", "[EPIC] Run-of-Show Builder", "P2",
     "Build an ordered day-of timeline that drives event execution."),
    ("portfolio_tagging", "[EPIC] Portfolio-Company Tagging", "P1",
     "Tag guests and companies to the firm's portfolio and network — the "
     "intelligence foundation."),
    ("recap", "[EPIC] Post-Event Recap & Insights", "P2",
     "Produce a structured recap and insights after an event to close the "
     "learning loop."),
    ("admin", "[EPIC] Admin Dashboard", "P2",
     "The firm-level surface: event portfolio at a glance, settings, and "
     "seed/demo control."),
    ("ai_layer", "[EPIC] AI Assistant Layer", "P1",
     "The pervasive AI assistant surface that will drive planning and execution "
     "across Cue."),
]

# Sprint 1 tasks: (key, title, type, priority, objective, epic_key, [dep_keys])
SPRINT1 = [
    ("app_shell", "Build the app shell (navigation + layout)", "feature", "P1",
     "Stand up the Cue App shell: top-level layout, navigation, and routing that "
     "the event dashboard and detail pages plug into.",
     "event_workspace", []),
    ("auth_placeholder", "Auth placeholder (no production auth)", "feature", "P1",
     "Add a placeholder auth seam — a fake current-user/session — isolated behind "
     "an interface so real identity can drop in later. No real auth.",
     "admin", []),
    ("event_dashboard", "Event dashboard (list of events)", "feature", "P1",
     "Build the event dashboard: a list of events with status at a glance, the "
     "landing surface of the app.",
     "event_workspace", ["app_shell"]),
    ("create_event", "Create-event flow", "feature", "P1",
     "Build the flow to create a new event and its workspace from scratch, "
     "producing a persisted event record.",
     "event_workspace", ["app_shell"]),
    ("event_detail", "Basic event detail page", "feature", "P1",
     "Build the event detail page — the working surface for a single event, "
     "showing its core fields and linking to guests.",
     "event_workspace", ["create_event"]),
    ("guest_model", "Guest list model", "feature", "P1",
     "Define and implement the guest/attendee data model (guest, status, and a "
     "portfolio-tag seam) and render a basic guest list on an event.",
     "guests", ["event_detail"]),
    ("ai_placeholder", "AI assistant placeholder", "feature", "P2",
     "Add the AI assistant placeholder surface — the entry point where AI "
     "planning help will live. No real generation yet.",
     "ai_layer", ["app_shell"]),
    ("seed_data", "Seed demo data", "feature", "P2",
     "Seed realistic demo data (a few events, guests, portfolio tags) so every "
     "MVP surface is walkable end-to-end.",
     "admin", ["event_dashboard", "guest_model"]),
]


# ---------------------------------------------------------------------------
# Population
# ---------------------------------------------------------------------------

def _learn(m: Monday, title: str, content: str, entry_type: str, tags: list[str]) -> str:
    r = m.learn(
        content=content,
        title=title,
        entry_type=entry_type,
        tags=["cue-app", *tags],
        components=[_COMPONENT],
    )
    if not r.accepted:
        raise RuntimeError(f"learn() rejected {title!r}: {r.message}")
    print(f"  knowledge  {r.entry_id:<10} {title}")
    return r.entry_id


def _task(m: Monday, title: str, objective: str, task_type: str, priority: str,
          context: str, criteria: list[str], created_by: str) -> str:
    r = m.task(
        "create",
        title=title,
        objective=objective,
        task_type=task_type,
        priority=priority,
        created_by=created_by,
        context=context,
        acceptance_criteria=criteria,
    )
    if not r.success:
        raise RuntimeError(f"task create rejected {title!r}: {r.message}")
    print(f"  task       {r.task_id:<10} [{priority}] {title}")
    return r.task_id


def populate(m: Monday, workspace_path: str) -> dict:
    ids: dict = {"knowledge": {}, "epics": {}, "sprint1": {}}

    # ── Project registry (Monday.project) ────────────────────────────────
    pr = m.project(
        "register", name="cue-app", path=workspace_path,
        description="AI-native event-operations platform for VC firms",
        overwrite=True,
    )
    print(f"  project    cue-app → {pr.data.get('source_path', workspace_path)}")

    # ── Foundational knowledge (Monday.learn) ────────────────────────────
    print("\nKnowledge — charter, vision, users, positioning, scope, roadmap, risks, DoD:")
    ids["knowledge"]["overview"] = _learn(m, "Cue App — Overview & Current State", OVERVIEW, "documentation", ["overview"])
    ids["knowledge"]["charter"] = _learn(m, _SENTINEL_TITLE, CHARTER, "documentation", ["charter", "vision"])
    ids["knowledge"]["vision"] = _learn(m, "Cue App — Product Vision", VISION, "documentation", ["vision", "north-star"])
    ids["knowledge"]["users"] = _learn(m, "Cue App — Target Users", TARGET_USERS, "documentation", ["users", "personas"])
    ids["knowledge"]["positioning"] = _learn(m, "Cue App — Competitive Positioning", POSITIONING, "documentation", ["positioning", "competition"])
    ids["knowledge"]["mvp"] = _learn(m, "Cue App — MVP Scope", MVP_SCOPE, "documentation", ["mvp", "scope"])
    ids["knowledge"]["roadmap"] = _learn(m, "Cue App — V1 Roadmap", ROADMAP, "documentation", ["roadmap", "milestones"])
    ids["knowledge"]["risks"] = _learn(m, "Cue App — Risk Register", RISK_REGISTER, "documentation", ["risk", "register"])
    ids["knowledge"]["dod"] = _learn(m, "Cue App — Definition of Done", DEFINITION_OF_DONE, "decision", ["definition-of-done", "standard"])
    ids["knowledge"]["guardrails"] = _learn(m, "Cue App — Scope Guardrails", SCOPE_GUARDRAILS, "decision", ["scope", "guardrails"])

    # ── Epics (Monday.task) ──────────────────────────────────────────────
    print("\nEpics:")
    for key, title, priority, objective in EPICS:
        ids["epics"][key] = _task(
            m, title, objective, "feature", priority,
            context=f"Cue App epic. Component: {_COMPONENT}.",
            criteria=["All child work meets the Cue App Definition of Done.",
                      "Stays within MVP scope unless an explicit scope decision is recorded."],
            created_by=_OWNER,
        )

    # ── Sprint 1 tasks (Monday.task) ─────────────────────────────────────
    print("\nSprint 1 — MVP foundation tasks:")
    for key, title, ttype, priority, objective, epic_key, dep_keys in SPRINT1:
        epic_id = ids["epics"][epic_key]
        dep_ids = [ids["sprint1"][d] for d in dep_keys if d in ids["sprint1"]]
        ctx = f"Sprint 1 (MVP foundation). Epic: {epic_id} ({epic_key}). Component: {_COMPONENT}."
        if dep_ids:
            ctx += " Depends on: " + ", ".join(dep_ids) + "."
        ids["sprint1"][key] = _task(
            m, title, objective, ttype, priority,
            context=ctx,
            criteria=["Meets the Cue App Definition of Done.",
                      "Stays within MVP scope (no payments, integrations, or production auth)."],
            created_by=_ENG,
        )

    # ── Derived knowledge that references the created IDs ─────────────────
    print("\nKnowledge — backlog map + Sprint 1 plan (reference real IDs):")
    ids["knowledge"]["backlog"] = _learn(
        m, "Cue App — Engineering Backlog", _backlog_overview(ids), "documentation",
        ["backlog", "epics"],
    )
    ids["knowledge"]["sprint1_plan"] = _learn(
        m, "Cue App — Sprint 1 Plan", _sprint1_plan(ids), "sprint",
        ["sprint-1", "kickoff"],
    )

    return ids


def _backlog_overview(ids: dict) -> str:
    lines = [
        "# Cue App — Engineering Backlog", "",
        "The live backlog lives as MondayOS tasks. This is the structural map:",
        "10 epics, with the Sprint 1 foundation tasks called out. Query live state",
        "with `monday task list`.", "",
        "## Epics",
    ]
    for key, title, priority, objective in EPICS:
        lines.append(f"- `{ids['epics'][key]}` [{priority}] {title}")
        lines.append(f"    {objective}")
    lines += ["", "## Sprint 1 tasks (MVP foundation)"]
    epic_titles = {k: t for k, t, _, _ in EPICS}
    for key, title, ttype, priority, objective, epic_key, dep_keys in SPRINT1:
        tid = ids["sprint1"][key]
        dep_ids = [ids["sprint1"][d] for d in dep_keys if d in ids["sprint1"]]
        dep_str = f"  ⟵ depends on {', '.join(dep_ids)}" if dep_ids else ""
        lines.append(f"- `{tid}` [{priority}] {title}  (epic: {epic_titles[epic_key]}){dep_str}")
    lines += ["",
              "Epics beyond Sprint 1 are sequenced in the V1 Roadmap knowledge entry.",
              ""]
    return "\n".join(lines)


def _sprint1_plan(ids: dict) -> str:
    s = ids["sprint1"]
    lines = [
        "# Cue App — Sprint 1 Plan", "",
        "## Goal",
        "Build the MVP foundation: a walkable product skeleton with real navigation,",
        "a persisted event + guest data shape, and the AI assistant surface stubbed in.",
        "",
        "## Scope (in this sprint)",
    ]
    order = ["app_shell", "auth_placeholder", "event_dashboard", "create_event",
             "event_detail", "guest_model", "ai_placeholder", "seed_data"]
    title_by_key = {k: t for k, t, *_ in SPRINT1}
    for key in order:
        lines.append(f"- `{s[key]}`  {title_by_key[key]}")
    lines += [
        "",
        "## Build order (from dependencies)",
        "1. App shell — everything plugs into it.",
        "2. Auth placeholder — a user/session seam, isolated for later real auth.",
        "3. Event dashboard + create-event flow — the event lifecycle entry points.",
        "4. Event detail page — the working surface for one event.",
        "5. Guest list model — the portfolio-aware data foundation.",
        "6. AI assistant placeholder — the surface the AI wedge will fill.",
        "7. Seed demo data — makes every surface walkable end-to-end.",
        "",
        "## Explicitly NOT in Sprint 1 (see Scope Guardrails)",
        "- No payments / ticketing.",
        "- No complex integrations (CRM, calendar, email).",
        "- No production authentication.",
        "- No overbuilding — each surface is a credible slice, not a finished module.",
        "",
        "## Exit criteria",
        "- A user can navigate the app shell, see the event dashboard, create an",
        "  event, open its detail page, and view a seeded guest list.",
        "- The AI assistant placeholder surface is present.",
        "- Seed demo data makes every MVP surface walkable.",
        "- Every Sprint 1 task meets the Cue App Definition of Done.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Committed index (durable map of the workspace)
# ---------------------------------------------------------------------------

def write_index(ids: dict, out_path: Path) -> None:
    k = ids["knowledge"]
    lines = [
        "# Cue App Product Workspace — Index",
        "",
        "Cue App is an AI-native event-operations platform for VC firms, managed",
        "entirely by MondayOS. Its complete product-management structure lives as",
        "**structured knowledge and tasks** in the MondayOS stores, created via the",
        "public API by [`setup_workspace.py`](setup_workspace.py). This file is a",
        "generated index — the source of truth is the MondayOS knowledge base and",
        "task system.",
        "",
        "## Explore Cue App using MondayOS alone",
        "```bash",
        "monday search cue-app                     # everything about Cue App",
        "monday ask \"What is Cue App and who is it for?\"",
        "monday task list                          # the live backlog (epics + Sprint 1)",
        "monday advise                             # what to work on next",
        "```",
        "",
        "## Knowledge entries",
        f"- Overview & current state — `{k.get('overview','')}`",
        f"- Project Charter (vision, goals, constraints, metrics) — `{k.get('charter','')}`",
        f"- Product Vision — `{k.get('vision','')}`",
        f"- Target Users — `{k.get('users','')}`",
        f"- Competitive Positioning — `{k.get('positioning','')}`",
        f"- MVP Scope — `{k.get('mvp','')}`",
        f"- V1 Roadmap — `{k.get('roadmap','')}`",
        f"- Risk Register — `{k.get('risks','')}`",
        f"- Definition of Done — `{k.get('dod','')}`",
        f"- Scope Guardrails (decision) — `{k.get('guardrails','')}`",
        f"- Engineering Backlog map — `{k.get('backlog','')}`",
        f"- Sprint 1 Plan — `{k.get('sprint1_plan','')}`",
        "",
        "## Epics",
    ]
    for key, title, priority, _ in EPICS:
        lines.append(f"- `{ids['epics'][key]}` [{priority}] {title}")
    lines += ["", "## Sprint 1 — MVP foundation tasks"]
    title_by_key = {key: title for key, title, *_ in SPRINT1}
    order = ["app_shell", "auth_placeholder", "event_dashboard", "create_event",
             "event_detail", "guest_model", "ai_placeholder", "seed_data"]
    for key in order:
        lines.append(f"- `{ids['sprint1'][key]}` {title_by_key[key]}")
    lines += ["",
              "*Regenerate this workspace with* "
              "`python projects/cue-app/setup_workspace.py --force`.",
              ""]
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nIndex written: {out_path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Populate the Cue App product workspace in MondayOS.")
    parser.add_argument("--project-root", default=str(_repo_root()),
                        help="MondayOS project root (default: the repo root).")
    parser.add_argument("--workspace-path", default=str(_workspace_dir()),
                        help="Path registered as the Cue App project home.")
    parser.add_argument("--force", action="store_true",
                        help="Repopulate even if the workspace already exists.")
    args = parser.parse_args(argv)

    root = Path(args.project_root)
    m = Monday(MondayConfig(project_root=root))

    existing = m.search(_SENTINEL_TITLE, limit=5)
    if any(r.get("title") == _SENTINEL_TITLE for r in existing.results) and not args.force:
        print("Cue App workspace already exists in this MondayOS instance.")
        print("Re-run with --force to rebuild it.")
        return 0

    print(f"Populating Cue App product workspace in: {root}")
    ids = populate(m, args.workspace_path)
    write_index(ids, _workspace_dir() / "PRODUCT_WORKSPACE.md")

    n_tasks = len(ids["epics"]) + len(ids["sprint1"])
    n_know = len(ids["knowledge"])
    print(f"\nDone. {n_know} knowledge entries, {n_tasks} tasks "
          f"({len(ids['epics'])} epics + {len(ids['sprint1'])} Sprint 1 tasks). "
          f"Explore with: monday --project-root {root} ask \"What is Cue App?\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
