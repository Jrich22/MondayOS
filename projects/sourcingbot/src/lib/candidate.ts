/**
 * Candidate — the PERSISTENT PERSON.
 *
 * A Candidate exists independently of any requisition and is never duplicated
 * per req. Everything here is a fact about the human being: identity, contact,
 * career history, skills. Nothing in this module knows what a requisition is,
 * and that is deliberate — the moment Candidate gains a `reqId` or a `stage`,
 * the model has collapsed and cross-req history is lost.
 *
 * Req-scoped judgement lives in req-candidate.ts. See docs/DATA_MODEL.md.
 */
import type { Candidate, CandidateOrigin, CandidateRole } from "./types";
import { newId, nowIso } from "./ids";

export interface NewCandidateInput {
  fullName: string;
  headline?: string;
  location?: string;
  email?: string;
  linkedInUrl?: string;
  roles?: CandidateRole[];
  skills?: string[];
  origin: CandidateOrigin;
  notes?: string;
}

export function newCandidate(input: NewCandidateInput): Candidate {
  const at = nowIso();
  return {
    id: newId("cand"),
    fullName: input.fullName.trim(),
    headline: input.headline?.trim() ?? "",
    location: input.location?.trim() ?? "",
    email: input.email?.trim() || undefined,
    linkedInUrl: input.linkedInUrl?.trim() || undefined,
    roles: input.roles ?? [],
    skills: normalizeSkills(input.skills),
    origin: input.origin,
    notes: input.notes?.trim() ?? "",
    createdAt: at,
    updatedAt: at,
  };
}

export function updateCandidate(
  candidate: Candidate,
  changes: Partial<Omit<Candidate, "id" | "createdAt">>,
): Candidate {
  return {
    ...candidate,
    ...changes,
    ...(changes.skills ? { skills: normalizeSkills(changes.skills) } : {}),
    id: candidate.id,
    createdAt: candidate.createdAt,
    updatedAt: nowIso(),
  };
}

export function currentRole(candidate: Candidate): CandidateRole | null {
  const open = candidate.roles.filter((r) => !r.endedAt);
  if (open.length > 0) {
    return [...open].sort((a, b) => b.startedAt.localeCompare(a.startedAt))[0];
  }
  if (candidate.roles.length === 0) return null;
  return [...candidate.roles].sort((a, b) =>
    (b.endedAt ?? b.startedAt).localeCompare(a.endedAt ?? a.startedAt),
  )[0];
}

export function currentCompany(candidate: Candidate): string {
  return currentRole(candidate)?.company ?? "";
}

/**
 * Identity key used to spot the same human arriving twice.
 *
 * Email is authoritative when present; otherwise fall back to a normalized
 * name + current company pair. Deliberately conservative: this reports a
 * *possible* duplicate for a human to confirm, and never auto-merges. Merging
 * people wrongly is far more damaging than carrying a duplicate for a day.
 */
export function identityKey(candidate: Candidate): string {
  const email = candidate.email?.trim().toLowerCase();
  if (email) return `email:${email}`;
  const name = candidate.fullName.trim().toLowerCase().replace(/\s+/g, " ");
  const company = currentCompany(candidate).trim().toLowerCase();
  return `name:${name}|co:${company}`;
}

export function findPossibleDuplicates(
  candidate: Candidate,
  existing: Candidate[],
): Candidate[] {
  const key = identityKey(candidate);
  return existing.filter((other) => other.id !== candidate.id && identityKey(other) === key);
}

/**
 * Talent concentration — how many of these people sit at each company.
 *
 * The first analytic the persistent-person model makes possible: it is only
 * meaningful because one person appears once, not once per requisition.
 */
export function talentConcentration(candidates: Candidate[]): Array<{
  company: string;
  count: number;
}> {
  const counts = new Map<string, number>();
  for (const c of candidates) {
    const company = currentCompany(c);
    if (!company) continue;
    counts.set(company, (counts.get(company) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([company, count]) => ({ company, count }))
    .sort((a, b) => b.count - a.count || a.company.localeCompare(b.company));
}

export function searchCandidates(candidates: Candidate[], query: string): Candidate[] {
  const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (terms.length === 0) return candidates;
  return candidates.filter((c) => {
    const haystack = [c.fullName, c.headline, c.location, currentCompany(c), ...c.skills]
      .join(" ")
      .toLowerCase();
    return terms.every((t) => haystack.includes(t));
  });
}

function normalizeSkills(skills: string[] | undefined): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of skills ?? []) {
    const value = raw.trim();
    if (!value) continue;
    const key = value.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(value);
  }
  return out;
}
