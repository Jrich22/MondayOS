/**
 * Sourcing Brief — the structured, reusable search definition for one Req.
 *
 * The brief is deliberately structured rather than free-text: requirements are
 * addressable objects with ids, so a ReqCandidate's assessment can cite the
 * exact requirement it answers, and `version` lets an evaluation record which
 * revision of the brief it was made against. A brief edited after an evaluation
 * does not silently rewrite history — see `isStaleAgainst`.
 */
import type { BriefRequirement, SourcingBrief } from "./types";
import { newId, nowIso } from "./ids";

export interface NewBriefInput {
  reqId: string;
  headline: string;
  seniority: SourcingBrief["seniority"];
  requirements?: Array<Omit<BriefRequirement, "id">>;
  targetCompanies?: string[];
  excludedCompanies?: string[];
  keywords?: string[];
  locations?: string[];
  outreachAngle?: string;
  targetIndustries?: string[];
  excludedIndustries?: string[];
  experienceGuidance?: string;
}

const MIN_WEIGHT = 1;
const MAX_WEIGHT = 5;

export function clampWeight(weight: number): number {
  if (Number.isNaN(weight)) return MIN_WEIGHT;
  return Math.min(MAX_WEIGHT, Math.max(MIN_WEIGHT, Math.round(weight)));
}

export function newRequirement(input: Omit<BriefRequirement, "id">): BriefRequirement {
  return {
    id: newId("rq"),
    label: input.label.trim(),
    kind: input.kind,
    weight: clampWeight(input.weight),
  };
}

export function newBrief(input: NewBriefInput): SourcingBrief {
  const at = nowIso();
  return {
    id: newId("brief"),
    reqId: input.reqId,
    version: 1,
    headline: input.headline.trim(),
    seniority: input.seniority,
    requirements: (input.requirements ?? []).map(newRequirement),
    targetCompanies: dedupeTrimmed(input.targetCompanies),
    excludedCompanies: dedupeTrimmed(input.excludedCompanies),
    keywords: dedupeTrimmed(input.keywords),
    locations: dedupeTrimmed(input.locations),
    outreachAngle: input.outreachAngle?.trim() ?? "",
    targetIndustries: dedupeTrimmed(input.targetIndustries),
    excludedIndustries: dedupeTrimmed(input.excludedIndustries),
    experienceGuidance: input.experienceGuidance?.trim() ?? "",
    createdAt: at,
    updatedAt: at,
  };
}

/** A blank brief for a newly created req, ready to author into. */
export function newDraftBrief(reqId: string): SourcingBrief {
  return newBrief({ reqId, headline: "", seniority: "mid" });
}

/** List fields the authoring surface edits as tag inputs. */
export type BriefListField =
  | "targetCompanies"
  | "excludedCompanies"
  | "targetIndustries"
  | "excludedIndustries"
  | "keywords"
  | "locations";

/** Add a value to one of the brief's list fields, deduped and version-bumped. */
export function addToList(
  brief: SourcingBrief,
  field: BriefListField,
  value: string,
): SourcingBrief {
  const clean = value.trim();
  if (!clean) return brief;
  const current = brief[field] ?? [];
  if (current.some((v) => v.toLowerCase() === clean.toLowerCase())) return brief;
  return reviseBrief(brief, { [field]: [...current, clean] });
}

/** Remove a value from one of the brief's list fields. */
export function removeFromList(
  brief: SourcingBrief,
  field: BriefListField,
  value: string,
): SourcingBrief {
  const current = brief[field] ?? [];
  return reviseBrief(brief, { [field]: current.filter((v) => v !== value) });
}

/**
 * Apply a content change and bump the version.
 *
 * Every mutation goes through here so a brief can never change without its
 * version moving — that invariant is what makes `isStaleAgainst` trustworthy.
 */
export function reviseBrief(
  brief: SourcingBrief,
  changes: Partial<
    Pick<
      SourcingBrief,
      | "headline"
      | "seniority"
      | "requirements"
      | "targetCompanies"
      | "excludedCompanies"
      | "keywords"
      | "locations"
      | "outreachAngle"
      | "targetIndustries"
      | "excludedIndustries"
      | "experienceGuidance"
    >
  >,
): SourcingBrief {
  return {
    ...brief,
    ...changes,
    version: brief.version + 1,
    updatedAt: nowIso(),
  };
}

export function addRequirement(
  brief: SourcingBrief,
  input: Omit<BriefRequirement, "id">,
): SourcingBrief {
  return reviseBrief(brief, { requirements: [...brief.requirements, newRequirement(input)] });
}

export function removeRequirement(brief: SourcingBrief, requirementId: string): SourcingBrief {
  return reviseBrief(brief, {
    requirements: brief.requirements.filter((r) => r.id !== requirementId),
  });
}

/**
 * Change a requirement's weight.
 *
 * Weight only affects ranking among *preferred* requirements — `computeFitScore`
 * treats every required item as pass/fail regardless of weight (ADR-006). The
 * authoring surface still allows setting it on a must-have so the number does
 * not appear to be ignored arbitrarily, and so it carries meaning if that
 * requirement is later switched to preferred.
 *
 * Routes through `reviseBrief`, so the version bumps and existing evaluations
 * are correctly flagged for reassessment rather than silently rescored.
 */
export function setRequirementWeight(
  brief: SourcingBrief,
  requirementId: string,
  weight: number,
): SourcingBrief {
  const target = brief.requirements.find((r) => r.id === requirementId);
  if (!target || target.weight === clampWeight(weight)) return brief;
  return reviseBrief(brief, {
    requirements: brief.requirements.map((r) =>
      r.id === requirementId ? { ...r, weight: clampWeight(weight) } : r,
    ),
  });
}

/** Move a requirement between must-have and nice-to-have. */
export function setRequirementKind(
  brief: SourcingBrief,
  requirementId: string,
  kind: BriefRequirement["kind"],
): SourcingBrief {
  const target = brief.requirements.find((r) => r.id === requirementId);
  if (!target || target.kind === kind) return brief;
  return reviseBrief(brief, {
    requirements: brief.requirements.map((r) =>
      r.id === requirementId ? { ...r, kind } : r,
    ),
  });
}

export function requiredRequirements(brief: SourcingBrief): BriefRequirement[] {
  return brief.requirements.filter((r) => r.kind === "required");
}

/** True when an evaluation made at `evaluatedVersion` predates the current brief. */
export function isStaleAgainst(brief: SourcingBrief, evaluatedVersion: number): boolean {
  return evaluatedVersion < brief.version;
}

/**
 * Readiness gate: a brief drives sourcing only once it can actually
 * discriminate. Without a headline and at least one required requirement,
 * every candidate trivially "fits", which is worse than no brief at all.
 */
export function isSourcingReady(brief: SourcingBrief): boolean {
  return brief.headline.trim().length > 0 && requiredRequirements(brief).length > 0;
}

export function briefReadinessIssues(brief: SourcingBrief): string[] {
  const issues: string[] = [];
  if (!brief.headline.trim()) issues.push("Add a headline describing the search.");
  if (requiredRequirements(brief).length === 0) {
    issues.push("Add at least one required requirement so the brief can discriminate.");
  }
  return issues;
}

function dedupeTrimmed(values: string[] | undefined): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of values ?? []) {
    const value = raw.trim();
    if (!value) continue;
    const key = value.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(value);
  }
  return out;
}
