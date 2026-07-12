import { useMemo } from "react";
import type {
  CueEvent,
  EventStatus,
  Guest,
  GuestRole,
  RsvpStatus,
} from "./types";
import { guestCompany } from "./guests-select";
import { useEvents } from "./store";
import { useAllGuests } from "./guests";

/**
 * Relationship Intelligence — the Person model (TASK-0044).
 *
 * Cue's long-term advantage is not a guest list; it is *organizational memory*.
 * A `Guest` is a single attendance record on a single event. A `Person` is the
 * persistent human that exists across every event — and every event enriches
 * them. This module is the seam that projects the flat, per-event `Guest[]` into
 * a set of persistent `Person` records: it resolves identity across events,
 * folds each appearance into one profile, and derives the interests,
 * organizations, and cross-event history the intelligence surfaces read.
 *
 * This is a *projection*, not a new store. People are derived from the guest and
 * event stores on demand (see `usePeople`), so there is nothing new to persist
 * and every check-in, edit, or new event automatically enriches the Person. When
 * a real backend lands, only this derivation changes; the surfaces keep calling
 * `usePeople()` / `usePerson()`.
 *
 * Everything here is a pure function over `Guest[]` + `CueEvent[]`, so the
 * identity-resolution and enrichment rules live in one auditable, unit-testable
 * place — the same split the rest of the app uses (lib/mission, lib/rollcall).
 *
 * Deliberately NOT built: a graph database, an address book, a CRM. The graph is
 * represented with plain TypeScript models (see lib/person-graph) and all AI is
 * offline/templated (see lib/person-ai).
 */

// ---------------------------------------------------------------------------
// Appearance — one Person's involvement in one event
// ---------------------------------------------------------------------------

/**
 * A single event in a Person's history: the `Guest` record, flattened with the
 * event context needed to render a timeline and network without re-joining. One
 * Person has one appearance per event they were on.
 */
export interface PersonAppearance {
  guestId: string;
  eventId: string;
  eventTitle: string;
  eventStatus: EventStatus;
  /** ISO start of the event — the chronological anchor for the timeline. */
  startsAt: string;
  rsvp: RsvpStatus;
  checkedIn: boolean;
  checkedInAt?: string;
  vip: boolean;
  roles: GuestRole[];
  isSpeaker: boolean;
  /** Company resolved for this event (portfolio tie wins; see guestCompany). */
  company: string;
  title?: string;
  portfolioCompanyId?: string;
  tags: string[];
  thankYouSent: boolean;
  /** Non-empty event notes about this person, if any. */
  note?: string;
}

// ---------------------------------------------------------------------------
// Person — the persistent, cross-event profile
// ---------------------------------------------------------------------------

export interface PersonNote {
  eventId: string;
  eventTitle: string;
  text: string;
  kind: "internal" | "public";
}

export interface Person {
  /** Stable id derived from identity, safe for URLs (see `personId`). */
  id: string;
  /** The resolution key (email or normalized name) — internal, for grouping. */
  key: string;
  firstName: string;
  lastName: string;
  /** Preferred-name-aware display name. */
  displayName: string;
  /** Up to two initials for the photo placeholder. */
  initials: string;
  email?: string;
  phone?: string;
  /** Current company + title, from the most recent appearance. */
  company: string;
  title?: string;
  /** Distinct portfolio-company ids this person has ever been tied to. */
  portfolioCompanyIds: string[];
  /** Union of professional roles across all appearances. */
  roles: GuestRole[];
  /** Union of freeform tags across all appearances. */
  tags: string[];
  /** True if flagged VIP on any event. */
  vip: boolean;
  /** True if a speaker/keynote on any event. */
  isSpeaker: boolean;
  /** Every event they were on, earliest first. */
  appearances: PersonAppearance[];
  /** Appearances they actually attended (checked in). */
  eventsAttended: number;
  /** Total events they were on (invited or beyond). */
  eventsInvited: number;
  /** ISO start of their first-ever event, if any. */
  firstSeen?: string;
  /** ISO start of their most recent event, if any. */
  lastSeen?: string;
  /** Derived interest themes (see `deriveInterests`). */
  interests: string[];
  /** Derived affiliations / networks (see `deriveOrganizations`). */
  organizations: string[];
  /** Notes captured about them at events, newest first. */
  notes: PersonNote[];
}

// ---------------------------------------------------------------------------
// Identity resolution
// ---------------------------------------------------------------------------

/**
 * The key that decides "same human across events". Email is the strong signal
 * (the seed emails are stable per person, and a real import would key on it);
 * absent an email we fall back to a normalized full name. Kept deliberately
 * simple — this is memory, not identity-graph fuzzy-matching.
 */
export function personKey(g: Guest): string {
  const email = g.identity.email?.trim().toLowerCase();
  if (email) return email;
  return `${g.identity.firstName} ${g.identity.lastName}`.trim().toLowerCase();
}

/** A stable, URL-safe id for a resolution key (djb2 → base36). */
export function personId(key: string): string {
  let h = 5381;
  for (let i = 0; i < key.length; i++) {
    h = (Math.imul(h, 33) + key.charCodeAt(i)) | 0;
  }
  return `psn-${(h >>> 0).toString(36)}`;
}

/** Convenience: the Person id a given Guest resolves to. */
export function personIdForGuest(g: Guest): string {
  return personId(personKey(g));
}

const SPEAKER_TAGS = ["speaker", "keynote"];

function isSpeakerGuest(g: Guest): boolean {
  return g.tags.some((t) => SPEAKER_TAGS.includes(t));
}

// ---------------------------------------------------------------------------
// Enrichment: interests & organizations (deterministic, derived)
// ---------------------------------------------------------------------------

/** Loose sector for each portfolio company — powers interest + network themes. */
export const PORTFOLIO_SECTOR: Record<string, string> = {
  "co-lattice": "AI Infrastructure",
  "co-fathom": "AI Infrastructure",
  "co-northwind": "AI Infrastructure",
  "co-cobalt": "Data",
  "co-arclight": "Enterprise",
  "co-meridian": "Healthcare",
};

/** Map a raw event/guest tag to an interest theme, or undefined to ignore it. */
function tagInterest(tag: string): string | undefined {
  switch (tag) {
    case "ai":
    case "infra":
    case "inference":
      return "AI Infrastructure";
    case "series-a":
    case "series-b":
      return "Fundraising";
    case "hiring":
    case "talent":
      return "Talent";
    case "keynote":
    case "speaker":
      return "Thought Leadership";
    default:
      return undefined;
  }
}

/** Map an event classification to an interest theme. */
function classificationInterest(c: CueEvent["classification"]): string | undefined {
  switch (c) {
    case "founder":
      return "Founders";
    case "investor":
      return "Investing";
    case "recruiting":
      return "Talent";
    case "community":
      return "Community";
    default:
      return undefined;
  }
}

/**
 * The interest themes a person's history implies: portfolio sectors they're tied
 * to, themes from the events they attend, and themes from their tags. Ordered by
 * how often each theme recurs so the strongest signals lead, capped so it reads
 * as a profile, not a word cloud.
 */
export function deriveInterests(
  appearances: PersonAppearance[],
  eventsById: Map<string, CueEvent>,
  limit = 5,
): string[] {
  const counts = new Map<string, number>();
  const bump = (theme?: string) => {
    if (theme) counts.set(theme, (counts.get(theme) ?? 0) + 1);
  };
  for (const a of appearances) {
    if (a.portfolioCompanyId) bump(PORTFOLIO_SECTOR[a.portfolioCompanyId]);
    const ev = eventsById.get(a.eventId);
    if (ev) bump(classificationInterest(ev.classification));
    for (const t of a.tags) bump(tagInterest(t));
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, limit)
    .map(([theme]) => theme);
}

/**
 * The networks / affiliations a person belongs to, derived from their roles and
 * portfolio ties. This is the "Organizations" dimension of the relationship
 * graph — human-readable groups a person can share with others, not a CRM field.
 */
export function deriveOrganizations(
  roles: GuestRole[],
  portfolioCompanyIds: string[],
): string[] {
  const orgs: string[] = [];
  if (portfolioCompanyIds.length > 0) orgs.push("Portfolio Network");
  if (roles.includes("founder")) orgs.push("Founders Circle");
  if (roles.includes("investor")) orgs.push("Investor Network");
  if (roles.includes("internal")) orgs.push("Cue Ventures");
  if (roles.includes("sponsor")) orgs.push("Partner Network");
  if (roles.includes("press")) orgs.push("Press Corps");
  return orgs;
}

// ---------------------------------------------------------------------------
// Display helpers
// ---------------------------------------------------------------------------

/** Preferred-name-aware display name for a person. */
export function personDisplayName(p: Person): string {
  return p.displayName;
}

// ---------------------------------------------------------------------------
// Projection: Guest[] + CueEvent[] → Person[]
// ---------------------------------------------------------------------------

function firstNonEmpty(...vals: (string | undefined)[]): string | undefined {
  for (const v of vals) {
    const t = v?.trim();
    if (t) return t;
  }
  return undefined;
}

/**
 * Resolve the flat roster into persistent people. One pass groups guests by
 * identity key; a second pass folds each group into a `Person`, taking current
 * company/title from the most recent appearance and unioning roles, tags, and
 * portfolio ties across their whole history.
 */
export function buildPeople(guests: Guest[], events: CueEvent[]): Person[] {
  const eventsById = new Map(events.map((e) => [e.id, e]));

  // Group guests (appearances) by resolved person key.
  const groups = new Map<string, Guest[]>();
  for (const g of guests) {
    const key = personKey(g);
    const list = groups.get(key);
    if (list) list.push(g);
    else groups.set(key, [g]);
  }

  const people: Person[] = [];
  for (const [key, roster] of groups) {
    const appearances: PersonAppearance[] = roster.map((g) => {
      const ev = eventsById.get(g.eventId);
      const note = firstNonEmpty(g.notes.internal, g.notes.public);
      return {
        guestId: g.id,
        eventId: g.eventId,
        eventTitle: ev?.title ?? "Unknown event",
        eventStatus: ev?.status ?? "draft",
        startsAt: ev?.startsAt ?? g.createdAt,
        rsvp: g.attendance.rsvp,
        checkedIn: g.attendance.checkedIn,
        checkedInAt: g.attendance.checkedInAt,
        vip: g.vip,
        roles: g.roles,
        isSpeaker: isSpeakerGuest(g),
        company: guestCompany(g, ev?.portfolio ?? []),
        title: g.professional.jobTitle,
        portfolioCompanyId: g.professional.portfolioCompanyId,
        tags: g.tags,
        thankYouSent: g.communication.thankYouSent,
        note,
      };
    });

    // Chronological history, earliest event first.
    appearances.sort((a, b) => Date.parse(a.startsAt) - Date.parse(b.startsAt));

    // Latest appearance drives "current" company/title.
    const latest = appearances[appearances.length - 1];
    const anyGuest = roster[0];

    const roles = unionRoles(roster);
    const tags = unionTags(roster);
    const portfolioCompanyIds = [
      ...new Set(
        roster
          .map((g) => g.professional.portfolioCompanyId)
          .filter((id): id is string => Boolean(id)),
      ),
    ];

    const firstName = anyGuest.identity.firstName;
    const lastName = anyGuest.identity.lastName;
    const displayFirst = anyGuest.identity.preferredName?.trim() || firstName;

    const notes: PersonNote[] = [];
    for (const g of roster) {
      const ev = eventsById.get(g.eventId);
      const title = ev?.title ?? "Unknown event";
      if (g.notes.internal?.trim())
        notes.push({ eventId: g.eventId, eventTitle: title, text: g.notes.internal.trim(), kind: "internal" });
      if (g.notes.public?.trim())
        notes.push({ eventId: g.eventId, eventTitle: title, text: g.notes.public.trim(), kind: "public" });
    }
    notes.sort((a, b) => {
      const ea = eventsById.get(a.eventId)?.startsAt ?? "";
      const eb = eventsById.get(b.eventId)?.startsAt ?? "";
      return Date.parse(eb) - Date.parse(ea);
    });

    people.push({
      id: personId(key),
      key,
      firstName,
      lastName,
      displayName: `${displayFirst} ${lastName}`.trim(),
      initials: initialsFrom(firstName, lastName),
      email: firstNonEmpty(...roster.map((g) => g.identity.email)),
      phone: firstNonEmpty(...roster.map((g) => g.identity.phone)),
      company: latest.company || firstNonEmpty(...roster.map((g) => g.professional.company)) || "",
      title: latest.title ?? firstNonEmpty(...roster.map((g) => g.professional.jobTitle)),
      portfolioCompanyIds,
      roles,
      tags,
      vip: roster.some((g) => g.vip),
      isSpeaker: roster.some(isSpeakerGuest),
      appearances,
      eventsAttended: appearances.filter((a) => a.checkedIn).length,
      eventsInvited: appearances.length,
      firstSeen: appearances[0]?.startsAt,
      lastSeen: latest?.startsAt,
      interests: deriveInterests(appearances, eventsById),
      organizations: deriveOrganizations(roles, portfolioCompanyIds),
      notes,
    });
  }

  // Most-connected people first (they anchor the directory), then by name.
  people.sort(
    (a, b) =>
      b.eventsInvited - a.eventsInvited ||
      a.lastName.localeCompare(b.lastName) ||
      a.firstName.localeCompare(b.firstName),
  );
  return people;
}

function unionRoles(roster: Guest[]): GuestRole[] {
  const set = new Set<GuestRole>();
  for (const g of roster) for (const r of g.roles) set.add(r);
  return [...set];
}

function unionTags(roster: Guest[]): string[] {
  const set = new Set<string>();
  for (const g of roster) for (const t of g.tags) set.add(t);
  return [...set].sort((a, b) => a.localeCompare(b));
}

function initialsFrom(first: string, last: string): string {
  const a = first.trim()[0] ?? "";
  const b = last.trim()[0] ?? "";
  return (a + b).toUpperCase() || "?";
}

// ---------------------------------------------------------------------------
// React hooks (reactive projection over the guest + event stores)
// ---------------------------------------------------------------------------

/** The full, reactive set of people, resolved from the guest + event stores. */
export function usePeople(): Person[] {
  const guests = useAllGuests();
  const events = useEvents();
  return useMemo(() => buildPeople(guests, events), [guests, events]);
}

/** A single person by id, reactive to store changes. */
export function usePerson(id: string | undefined): Person | undefined {
  const people = usePeople();
  return useMemo(
    () => (id ? people.find((p) => p.id === id) : undefined),
    [people, id],
  );
}
