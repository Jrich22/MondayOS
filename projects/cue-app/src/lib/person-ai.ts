import type { CueEvent } from "./types";
import type { Person } from "./people";
import { PORTFOLIO_SECTOR } from "./people";
import { coAttendees } from "./person-graph";

/**
 * Person AI (TASK-0044) — offline, templated insight, exactly like lib/ai and
 * lib/rollcall-ai. There is no model and no network: every insight, the profile
 * summary, and the event recommendations are *derived deterministically* from
 * the person's cross-event history, so they are always true for the record as it
 * stands and are fully unit-testable. When a real model is wired in later, only
 * this module changes; the profile UI stays put.
 *
 * These are the "organizational memory" payoffs the whole feature exists for:
 * "attends founder dinners", "frequently seen with healthcare founders", "strong
 * relationship with <you>", "recommend inviting to <event>".
 */

/** A single derived insight chip/line on a profile. */
export interface PersonInsight {
  id: string;
  text: string;
  /** Rough kind, so the UI can tint reliability vs. affinity vs. relationship. */
  kind: "history" | "affinity" | "relationship" | "reliability" | "recommendation";
}

/** The dominant sector across a person's portfolio ties, if there is one. */
function dominantSector(person: Person): string | undefined {
  const counts = new Map<string, number>();
  for (const id of person.portfolioCompanyIds) {
    const sector = PORTFOLIO_SECTOR[id];
    if (sector) counts.set(sector, (counts.get(sector) ?? 0) + 1);
  }
  let best: string | undefined;
  let bestN = 0;
  for (const [sector, n] of counts) {
    if (n > bestN) {
      best = sector;
      bestN = n;
    }
  }
  return best;
}

/** The sector most represented among the people this person is seen with. */
function networkSector(person: Person, people: Person[]): string | undefined {
  const ties = coAttendees(person, people, 20);
  const counts = new Map<string, number>();
  for (const t of ties) {
    for (const id of t.person.portfolioCompanyIds) {
      const sector = PORTFOLIO_SECTOR[id];
      if (sector) counts.set(sector, (counts.get(sector) ?? 0) + t.count);
    }
  }
  let best: string | undefined;
  let bestN = 0;
  for (const [sector, n] of counts) {
    if (n > bestN) {
      best = sector;
      bestN = n;
    }
  }
  return best;
}

/** How many events this person shares with a specific other person. */
function sharedEventCount(person: Person, other: Person): number {
  const otherEvents = new Set(other.appearances.map((a) => a.eventId));
  return person.appearances.filter((a) => otherEvents.has(a.eventId)).length;
}

/**
 * The offline insight stack for a profile, most telling first: how much history
 * we have, whether they're a reliable attendee, what kind of rooms they favor,
 * who they orbit, and their relationship to the current organizer. Each rule
 * fires only when its signal is real, so the list is never padded.
 *
 * `currentUser` (optional) anchors the relationship insight — "strong
 * relationship with <name>" — off shared events with the person running Cue.
 */
export function personInsights(
  person: Person,
  people: Person[],
  currentUser?: Person,
  limit = 5,
): PersonInsight[] {
  const out: PersonInsight[] = [];

  if (person.eventsInvited > 0) {
    const attended = person.eventsAttended;
    out.push({
      id: "history",
      kind: "history",
      text:
        attended > 0
          ? `Has attended ${attended} event${attended === 1 ? "" : "s"} with the firm.`
          : `On ${person.eventsInvited} guest list${person.eventsInvited === 1 ? "" : "s"} — not yet attended in person.`,
    });
  }

  // Reliability: check-in rate across events they were confirmed for.
  const confirmed = person.appearances.filter((a) => a.rsvp === "confirmed");
  if (confirmed.length >= 2) {
    const showed = confirmed.filter((a) => a.checkedIn).length;
    if (showed === confirmed.length) {
      out.push({
        id: "reliable",
        kind: "reliability",
        text: `Reliable — showed up to all ${confirmed.length} events they confirmed.`,
      });
    } else if (showed <= confirmed.length / 2) {
      out.push({
        id: "flaky",
        kind: "reliability",
        text: `Confirms often but attends ${showed} of ${confirmed.length} — worth a personal nudge.`,
      });
    }
  }

  // Affinity: the kind of rooms they favor.
  const sector = dominantSector(person);
  if (person.roles.includes("founder") && person.eventsAttended >= 2) {
    out.push({
      id: "founder-dinners",
      kind: "affinity",
      text: sector
        ? `Regular at ${sector.toLowerCase()} founder gatherings.`
        : "Regular on the founder-dinner circuit.",
    });
  }

  // Who they orbit.
  const netSector = networkSector(person, people);
  if (netSector) {
    out.push({
      id: "network-sector",
      kind: "affinity",
      text: `Frequently seen with ${netSector.toLowerCase()} founders.`,
    });
  }

  // Relationship to the organizer.
  if (currentUser && currentUser.id !== person.id) {
    const shared = sharedEventCount(person, currentUser);
    if (shared >= 2) {
      out.push({
        id: "relationship",
        kind: "relationship",
        text: `Strong relationship with ${currentUser.firstName} — ${shared} events together.`,
      });
    } else if (shared === 1) {
      out.push({
        id: "relationship",
        kind: "relationship",
        text: `Met ${currentUser.firstName} once — room to deepen the relationship.`,
      });
    }
  }

  if (person.vip) {
    out.push({
      id: "vip",
      kind: "history",
      text: "Flagged VIP — brief the host whenever they attend.",
    });
  }

  return out.slice(0, limit);
}

/**
 * A one-paragraph profile summary stitched from the strongest facts — the kind
 * of at-a-glance read an organizer wants before walking over to say hello.
 */
export function personSummary(person: Person, people: Person[]): string {
  const name = person.firstName;
  const parts: string[] = [];

  const role = person.company
    ? `${person.title ? `${person.title} at ` : ""}${person.company}`
    : person.title ?? "guest";
  parts.push(`${name} is ${role}.`);

  if (person.eventsAttended > 0) {
    parts.push(
      `They've joined ${person.eventsAttended} of ${person.eventsInvited} events they were invited to.`,
    );
  } else if (person.eventsInvited > 0) {
    parts.push(`Invited to ${person.eventsInvited} events; not yet attended.`);
  }

  const netSector = networkSector(person, people);
  if (netSector) {
    parts.push(`They tend to gravitate toward ${netSector.toLowerCase()} circles.`);
  } else if (person.interests.length > 0) {
    parts.push(`Interested in ${listPhrase(person.interests.slice(0, 3))}.`);
  }

  if (person.vip) parts.push("A VIP worth a warm welcome.");

  return parts.join(" ");
}

/** A recommended future event for a person, with a human "why". */
export interface EventRecommendation {
  event: CueEvent;
  reason: string;
  /** Match strength, for ordering. */
  score: number;
}

/**
 * Upcoming (and still-in-planning draft) events worth inviting this person to,
 * scored by fit with their history: portfolio-sector overlap, role/classification
 * match, and shared interests. Events they're already on are excluded — this is
 * about *widening* the relationship, not re-inviting. Drafts are included on
 * purpose: choosing who to invite is exactly the work you do while planning one.
 */
export function recommendedEvents(
  person: Person,
  events: CueEvent[],
  limit = 3,
): EventRecommendation[] {
  const alreadyOn = new Set(person.appearances.map((a) => a.eventId));
  const personSectors = new Set(
    person.portfolioCompanyIds
      .map((id) => PORTFOLIO_SECTOR[id])
      .filter((s): s is string => Boolean(s)),
  );
  const interests = new Set(person.interests);

  const recs: EventRecommendation[] = [];
  for (const ev of events) {
    if (ev.status !== "upcoming" && ev.status !== "draft") continue;
    if (alreadyOn.has(ev.id)) continue;

    let score = 0;
    const reasons: string[] = [];

    // Portfolio-sector overlap with the event's portfolio.
    const eventSectors = new Set(
      ev.portfolio.map((c) => PORTFOLIO_SECTOR[c.id]).filter((s): s is string => Boolean(s)),
    );
    const sectorHit = [...eventSectors].find((s) => personSectors.has(s));
    if (sectorHit) {
      score += 3;
      reasons.push(`${sectorHit.toLowerCase()} focus matches their portfolio`);
    }

    // Role / classification match.
    if (person.roles.includes("founder") && ev.classification === "founder") {
      score += 2;
      reasons.push("a founder gathering they'd fit");
    }
    if (person.roles.includes("investor") && ev.classification === "investor") {
      score += 2;
      reasons.push("an investor briefing in their lane");
    }

    // Shared interest via event tags.
    const interestTag = ev.tags.find((t) => interests.has(titleCase(t)));
    if (interestTag) {
      score += 1;
      reasons.push(`tagged "${interestTag}"`);
    }

    // VIPs are worth an invite to the flagship even without an exact match.
    if (person.vip && ev.tags.includes("flagship")) {
      score += 1;
      reasons.push("flagship worth a VIP invite");
    }

    if (score > 0) {
      recs.push({
        event: ev,
        score,
        reason: capitalize(reasons[0] ?? "fits their profile"),
      });
    }
  }

  recs.sort(
    (a, b) =>
      b.score - a.score ||
      Date.parse(a.event.startsAt) - Date.parse(b.event.startsAt),
  );
  return recs.slice(0, limit);
}

// --- small text helpers -----------------------------------------------------

function listPhrase(items: string[]): string {
  if (items.length === 0) return "";
  if (items.length === 1) return items[0];
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function titleCase(s: string): string {
  return s
    .split("-")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}
