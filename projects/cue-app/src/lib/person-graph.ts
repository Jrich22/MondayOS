import type { Person, PersonAppearance } from "./people";

/**
 * The relationship graph (TASK-0044) — represented with plain TypeScript models,
 * NOT a graph database. The edges the product cares about (Person ↔ Person,
 * Person ↔ Company, Person ↔ Organization, Person ↔ Event) are all *derived* from
 * shared appearances: two people are connected because they were in the same
 * rooms, and the more rooms they shared, the stronger the tie.
 *
 * Everything is a pure function over the already-projected `Person[]` (see
 * lib/people), so a profile page is presentation-only and the graph rules are
 * unit-testable in one place.
 */

/** A Person ↔ Person edge, weighted by the events the two people shared. */
export interface CoAttendee {
  person: Person;
  /** Ids of the events both people were on. */
  sharedEventIds: string[];
  /** Titles of those shared events (for display). */
  sharedEventTitles: string[];
  /** How many events they shared — the edge weight. */
  count: number;
}

/** A Person ↔ Company edge: a company seen across a person's network. */
export interface CompanyTie {
  name: string;
  /** How many people in the network represent this company. */
  count: number;
}

/** The full relationship neighborhood rendered on a profile. */
export interface PersonNetwork {
  /** People frequently seen together, strongest tie first. */
  coAttendees: CoAttendee[];
  /** Companies represented across that network, most-common first. */
  companies: CompanyTie[];
  /** Events this person attended (their own history), most recent first. */
  events: PersonAppearance[];
  /** Organizations shared with anyone in the network. */
  organizations: string[];
}

/** The set of event ids a person was on. */
function eventIdSet(p: Person): Set<string> {
  return new Set(p.appearances.map((a) => a.eventId));
}

/**
 * People who share at least one event with `person`, ranked by shared-event
 * count (then most-recent shared event, then name). This is the Person ↔ Person
 * edge set — "who is this person frequently seen with".
 */
export function coAttendees(
  person: Person,
  people: Person[],
  limit = 8,
): CoAttendee[] {
  const mine = eventIdSet(person);
  const titleById = new Map(person.appearances.map((a) => [a.eventId, a.eventTitle]));
  const startById = new Map(person.appearances.map((a) => [a.eventId, a.startsAt]));

  const ties: CoAttendee[] = [];
  for (const other of people) {
    if (other.id === person.id) continue;
    const shared: string[] = [];
    for (const a of other.appearances) {
      if (mine.has(a.eventId) && !shared.includes(a.eventId)) shared.push(a.eventId);
    }
    if (shared.length === 0) continue;
    shared.sort((x, y) => Date.parse(startById.get(y) ?? "") - Date.parse(startById.get(x) ?? ""));
    ties.push({
      person: other,
      sharedEventIds: shared,
      sharedEventTitles: shared.map((id) => titleById.get(id) ?? "Event"),
      count: shared.length,
    });
  }

  ties.sort(
    (a, b) =>
      b.count - a.count ||
      Date.parse(latestSharedStart(b, startById)) - Date.parse(latestSharedStart(a, startById)) ||
      a.person.lastName.localeCompare(b.person.lastName),
  );
  return ties.slice(0, limit);
}

function latestSharedStart(t: CoAttendee, startById: Map<string, string>): string {
  return t.sharedEventIds.map((id) => startById.get(id) ?? "").sort().reverse()[0] ?? "";
}

/**
 * The companies represented across a person's co-attendee network, most-common
 * first. Answers "which companies orbit this person" without a company entity —
 * it's read straight off who they're seen with.
 */
export function networkCompanies(ties: CoAttendee[], limit = 6): CompanyTie[] {
  const counts = new Map<string, number>();
  for (const t of ties) {
    const co = t.person.company.trim();
    if (co) counts.set(co, (counts.get(co) ?? 0) + 1);
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, limit)
    .map(([name, count]) => ({ name, count }));
}

/** Organizations this person shares with anyone in their co-attendee network. */
export function sharedOrganizations(person: Person, ties: CoAttendee[]): string[] {
  const mine = new Set(person.organizations);
  const shared = new Set<string>();
  for (const t of ties) {
    for (const org of t.person.organizations) {
      if (mine.has(org)) shared.add(org);
    }
  }
  return [...shared].sort((a, b) => a.localeCompare(b));
}

/**
 * Assemble the full relationship neighborhood for a profile: who they're seen
 * with, the companies around them, their own event history (most recent first),
 * and the organizations they share with their network.
 */
export function personNetwork(person: Person, people: Person[]): PersonNetwork {
  const ties = coAttendees(person, people);
  return {
    coAttendees: ties,
    companies: networkCompanies(ties),
    events: [...person.appearances].sort(
      (a, b) => Date.parse(b.startsAt) - Date.parse(a.startsAt),
    ),
    organizations: sharedOrganizations(person, ties),
  };
}
