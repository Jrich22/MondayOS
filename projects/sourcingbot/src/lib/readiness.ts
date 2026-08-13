/**
 * Req readiness — how complete a requisition is, and whether it can be sourced.
 *
 * This is a DERIVED VIEW over the existing `Req` and `SourcingBrief`. It stores
 * nothing and owns no entity: adding a "completeness" record would be a third
 * source of truth that could disagree with the two it summarises. Every value
 * here is computed on read (see docs/DECISIONS.md ADR-008).
 *
 * Two distinct questions, deliberately kept apart:
 *
 *   completeness — how much of the req has been authored (0-100%). A progress
 *                  signal for the recruiter. Nothing blocks on it.
 *   readiness    — can this actually drive a search? A hard gate: a req missing
 *                  a title or a single must-have cannot discriminate between
 *                  candidates, so opening it for sourcing would waste the
 *                  recruiter's time in a way a percentage does not convey.
 *
 * A req can be 90% complete and not ready (no must-haves), or 60% complete and
 * ready. Collapsing them into one number would hide exactly the case that
 * matters.
 */
import type { Req, SourcingBrief } from "./types";
import { requiredRequirements } from "./brief";

/** One authoring section, as shown in the readiness panel and section rail. */
export type SectionId =
  | "role"
  | "description"
  | "intake"
  | "targeting"
  | "requirements"
  | "keywords"
  | "goals";

export interface SectionStatus {
  id: SectionId;
  label: string;
  /** 0-1. Partial credit so a half-filled section reads as in progress. */
  progress: number;
  /** This section contains at least one item that gates sourcing. */
  essential: boolean;
  /** Everything still missing here, phrased as an action. */
  missing: string[];
  /**
   * The subset of `missing` that actually blocks sourcing.
   *
   * Kept separate from `missing` on purpose. A section can be essential without
   * every field in it being essential: `requirements` gates sourcing because a
   * req with no must-have cannot filter anyone out, but a missing nice-to-have
   * only costs you ranking, so it belongs in `missing` and not here. Deriving
   * blockers from section progress instead would silently make every optional
   * field mandatory.
   */
  blocking: string[];
}

export interface ReqReadiness {
  sections: SectionStatus[];
  /** 0-100, weighted across all sections. */
  completeness: number;
  /** True when every essential section is fully satisfied. */
  sourcingReady: boolean;
  /** Ordered, actionable — what to do next, most important first. */
  blockers: string[];
  suggestions: string[];
}

const filled = (v: string | undefined | null): boolean => Boolean(v && v.trim());
const anyOf = (v: string[] | undefined): boolean => Boolean(v && v.length > 0);

/** Fraction of the given checks that pass. */
function score(checks: boolean[]): number {
  if (checks.length === 0) return 1;
  return checks.filter(Boolean).length / checks.length;
}

export function evaluateReadiness(req: Req, brief: SourcingBrief | undefined): ReqReadiness {
  const b = brief;
  const musts = b ? requiredRequirements(b) : [];
  const nices = b ? b.requirements.filter((r) => r.kind === "preferred") : [];

  const roleMissing = [
    !filled(req.code) && "Add a requisition code",
    !filled(req.title) && "Add a role title",
    !filled(req.team) && "Add the owning team",
    !filled(req.location) && "Add a location",
  ].filter(Boolean) as string[];

  const requirementsBlocking = [
    !filled(b?.headline) && "Add a headline describing the search",
    musts.length === 0 && "Add at least one must-have",
  ].filter(Boolean) as string[];

  const sections: SectionStatus[] = [
    {
      id: "role",
      label: "Role basics",
      essential: true,
      progress: score([filled(req.code), filled(req.title), filled(req.team), filled(req.location)]),
      missing: roleMissing,
      // Every role basic is required to source: without them the req cannot be
      // identified or targeted at all.
      blocking: roleMissing,
    },
    {
      id: "description",
      label: "Job description",
      essential: false,
      progress: score([filled(req.jobDescription), (req.jobDescription ?? "").length > 200]),
      missing: [
        !filled(req.jobDescription) && "Add the full job description",
        filled(req.jobDescription) &&
          (req.jobDescription ?? "").length <= 200 &&
          "Expand the description — it is very short",
      ].filter(Boolean) as string[],
      blocking: [],
    },
    {
      id: "intake",
      label: "Intake notes",
      essential: false,
      progress: score([filled(req.intakeNotes), filled(req.hiringManager)]),
      missing: [
        !filled(req.hiringManager) && "Name the hiring manager",
        !filled(req.intakeNotes) && "Capture notes from the intake conversation",
      ].filter(Boolean) as string[],
      blocking: [],
    },
    {
      id: "targeting",
      label: "Targeting",
      essential: false,
      progress: score([
        anyOf(b?.locations),
        anyOf(b?.targetCompanies) || anyOf(b?.targetIndustries),
        anyOf(b?.excludedCompanies) || anyOf(b?.excludedIndustries),
      ]),
      missing: [
        !anyOf(b?.locations) && "Add at least one target location",
        !anyOf(b?.targetCompanies) &&
          !anyOf(b?.targetIndustries) &&
          "Add target companies or industries",
        !anyOf(b?.excludedCompanies) &&
          !anyOf(b?.excludedIndustries) &&
          "Note any exclusions — conflicts, portfolio companies",
      ].filter(Boolean) as string[],
      blocking: [],
    },
    {
      id: "requirements",
      label: "Must-haves & nice-to-haves",
      essential: true,
      progress: score([musts.length > 0, nices.length > 0, filled(b?.headline)]),
      missing: [
        ...requirementsBlocking,
        nices.length === 0 && "Add nice-to-haves so candidates can be ranked",
      ].filter(Boolean) as string[],
      // A missing nice-to-have costs ranking, not the ability to source.
      blocking: requirementsBlocking,
    },
    {
      id: "keywords",
      label: "Keywords & experience",
      essential: false,
      progress: score([anyOf(b?.keywords), filled(b?.experienceGuidance)]),
      missing: [
        !anyOf(b?.keywords) && "Add search keywords",
        !filled(b?.experienceGuidance) && "Add experience guidance",
      ].filter(Boolean) as string[],
      blocking: [],
    },
    {
      id: "goals",
      label: "Sourcing goals",
      essential: false,
      progress: score([
        Boolean(req.sourcingGoals?.targetCandidates),
        Boolean(req.sourcingGoals?.targetContacts) || filled(req.sourcingGoals?.notes),
      ]),
      missing: [
        !req.sourcingGoals?.targetCandidates && "Set a target number of candidates",
        !req.sourcingGoals?.targetContacts &&
          !filled(req.sourcingGoals?.notes) &&
          "Set a contact target or describe the goal",
      ].filter(Boolean) as string[],
      blocking: [],
    },
  ];

  const completeness = Math.round(
    (sections.reduce((sum, s) => sum + s.progress, 0) / sections.length) * 100,
  );

  const blockers = sections.flatMap((s) => s.blocking);
  const blockingSet = new Set(blockers);

  return {
    sections,
    completeness,
    sourcingReady: blockers.length === 0,
    blockers,
    // Everything else worth doing, minus what is already listed as a blocker.
    suggestions: sections.flatMap((s) => s.missing).filter((m) => !blockingSet.has(m)),
  };
}

/**
 * Whether the req may be opened for sourcing, and why not.
 *
 * Opening is gated on readiness rather than completeness: a req that cannot
 * discriminate between candidates will match everyone, which is worse than
 * having no req at all.
 */
export function canOpenForSourcing(
  req: Req,
  brief: SourcingBrief | undefined,
): { allowed: boolean; reasons: string[] } {
  if (req.status === "closed") {
    return { allowed: false, reasons: ["This requisition is closed."] };
  }
  if (req.status === "open") {
    return { allowed: false, reasons: ["This requisition is already open."] };
  }
  const readiness = evaluateReadiness(req, brief);
  return readiness.sourcingReady
    ? { allowed: true, reasons: [] }
    : { allowed: false, reasons: readiness.blockers };
}

/** Coarse band for the completeness ring. */
export function completenessTone(pct: number): "low" | "medium" | "high" {
  if (pct >= 80) return "high";
  if (pct >= 40) return "medium";
  return "low";
}
