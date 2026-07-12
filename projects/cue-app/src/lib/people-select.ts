import type { GuestRole } from "./types";
import type { Person } from "./people";

/**
 * Global people search, filtering, and sorting (TASK-0044). Where lib/guests-
 * select searches one event's roster, this searches the *whole persistent
 * directory* — every human the firm has ever hosted — across the dimensions the
 * relationship workspace is organized by: company, role, organization, portfolio,
 * tags, attendance, VIP, and speaker.
 *
 * Pure over `Person[]`, so the People page is presentation and the rules stay
 * testable in one place.
 */

export type PeopleSort = "connections" | "name" | "recent" | "events";

export interface PeopleFilters {
  query: string;
  role: GuestRole | null;
  company: string | null;
  organization: string | null;
  portfolioCompanyId: string | null;
  tag: string | null;
  interest: string | null;
  vipOnly: boolean;
  speakerOnly: boolean;
  attendedOnly: boolean;
}

export function emptyPeopleFilters(): PeopleFilters {
  return {
    query: "",
    role: null,
    company: null,
    organization: null,
    portfolioCompanyId: null,
    tag: null,
    interest: null,
    vipOnly: false,
    speakerOnly: false,
    attendedOnly: false,
  };
}

/** True when any filter (including search) is narrowing the directory. */
export function hasActivePeopleFilters(f: PeopleFilters): boolean {
  return (
    f.query.trim() !== "" ||
    f.role !== null ||
    f.company !== null ||
    f.organization !== null ||
    f.portfolioCompanyId !== null ||
    f.tag !== null ||
    f.interest !== null ||
    f.vipOnly ||
    f.speakerOnly ||
    f.attendedOnly
  );
}

/** Free-text match across a person's most identifying fields + their history. */
export function matchesPersonQuery(p: Person, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const haystack = [
    p.displayName,
    p.firstName,
    p.lastName,
    p.email ?? "",
    p.company,
    p.title ?? "",
    ...p.tags,
    ...p.interests,
    ...p.organizations,
    ...p.appearances.map((a) => `${a.company} ${a.eventTitle}`),
  ]
    .join(" ")
    .toLowerCase();
  return haystack.includes(q);
}

/** Filter then sort the directory. Pure over its inputs. */
export function selectPeople(
  people: Person[],
  filters: PeopleFilters,
  sort: PeopleSort,
): Person[] {
  const filtered = people.filter((p) => {
    if (!matchesPersonQuery(p, filters.query)) return false;
    if (filters.role && !p.roles.includes(filters.role)) return false;
    if (filters.company && p.company !== filters.company) return false;
    if (filters.organization && !p.organizations.includes(filters.organization)) return false;
    if (
      filters.portfolioCompanyId &&
      !p.portfolioCompanyIds.includes(filters.portfolioCompanyId)
    )
      return false;
    if (filters.tag && !p.tags.includes(filters.tag)) return false;
    if (filters.interest && !p.interests.includes(filters.interest)) return false;
    if (filters.vipOnly && !p.vip) return false;
    if (filters.speakerOnly && !p.isSpeaker) return false;
    if (filters.attendedOnly && p.eventsAttended === 0) return false;
    return true;
  });
  return sortPeople(filtered, sort);
}

export function sortPeople(people: Person[], sort: PeopleSort): Person[] {
  const byName = (a: Person, b: Person) =>
    a.lastName.localeCompare(b.lastName) || a.firstName.localeCompare(b.firstName);

  return [...people].sort((a, b) => {
    switch (sort) {
      case "name":
        return byName(a, b);
      case "recent": {
        const at = a.lastSeen ? Date.parse(a.lastSeen) : -Infinity;
        const bt = b.lastSeen ? Date.parse(b.lastSeen) : -Infinity;
        return bt - at || byName(a, b);
      }
      case "events":
        return b.eventsAttended - a.eventsAttended || byName(a, b);
      case "connections":
      default:
        // Most-connected first: total appearances, then VIPs float up.
        return (
          b.eventsInvited - a.eventsInvited ||
          Number(b.vip) - Number(a.vip) ||
          byName(a, b)
        );
    }
  });
}

// --- Facets (options built from the directory itself) -----------------------

export interface PeopleFacets {
  companies: string[];
  organizations: string[];
  tags: string[];
  interests: string[];
  /** Portfolio ties present, as {id,name} using the latest name seen. */
  portfolio: { id: string; name: string }[];
}

/**
 * Distinct filter options present in the directory, each sorted. Portfolio
 * options resolve an id to the company name a person currently shows for it.
 */
export function peopleFacets(people: Person[]): PeopleFacets {
  const companies = new Set<string>();
  const organizations = new Set<string>();
  const tags = new Set<string>();
  const interests = new Set<string>();
  const portfolio = new Map<string, string>();

  for (const p of people) {
    if (p.company.trim()) companies.add(p.company.trim());
    for (const o of p.organizations) organizations.add(o);
    for (const t of p.tags) tags.add(t);
    for (const i of p.interests) interests.add(i);
    for (const id of p.portfolioCompanyIds) {
      // Prefer the company label from an appearance carrying that tie.
      const appearance = p.appearances.find((a) => a.portfolioCompanyId === id && a.company);
      if (appearance && !portfolio.has(id)) portfolio.set(id, appearance.company);
      else if (!portfolio.has(id)) portfolio.set(id, id);
    }
  }

  const alpha = (a: string, b: string) => a.localeCompare(b);
  return {
    companies: [...companies].sort(alpha),
    organizations: [...organizations].sort(alpha),
    tags: [...tags].sort(alpha),
    interests: [...interests].sort(alpha),
    portfolio: [...portfolio.entries()]
      .map(([id, name]) => ({ id, name }))
      .sort((a, b) => a.name.localeCompare(b.name)),
  };
}

// --- Directory-wide summary (KPI row) ---------------------------------------

export interface PeopleSummary {
  total: number;
  attended: number;
  vips: number;
  /** People on more than one event — the recurring relationships. */
  recurring: number;
}

export function peopleSummary(people: Person[]): PeopleSummary {
  let attended = 0;
  let vips = 0;
  let recurring = 0;
  for (const p of people) {
    if (p.eventsAttended > 0) attended += 1;
    if (p.vip) vips += 1;
    if (p.eventsInvited > 1) recurring += 1;
  }
  return { total: people.length, attended, vips, recurring };
}
