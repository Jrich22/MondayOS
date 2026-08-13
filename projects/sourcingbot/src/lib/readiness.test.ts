/**
 * Readiness is a DERIVED view — it stores nothing and must never disagree with
 * the Req and Brief it summarises.
 *
 * The load-bearing distinction under test: completeness (how much is authored)
 * and readiness (can this discriminate between candidates) are separate. A req
 * can score high on one and fail the other, and the panel must not merge them.
 */
import { describe, it, expect, beforeEach } from "vitest";
import { canOpenForSourcing, completenessTone, evaluateReadiness } from "./readiness";
import { newDraftReq, newReq, transition, updateReq } from "./req";
import { addRequirement, newBrief, newDraftBrief, reviseBrief } from "./brief";
import { __resetIdCounter } from "./ids";
import type { Req, SourcingBrief } from "./types";

beforeEach(() => __resetIdCounter());

const baseReq = (): Req =>
  newReq({ code: "REQ-001", title: "Staff Engineer", team: "Infra", location: "Boston" });

function fullBrief(): SourcingBrief {
  let b = newBrief({
    reqId: "r",
    headline: "Platform engineers",
    seniority: "staff",
    requirements: [{ label: "7+ years", kind: "required", weight: 5 }],
    locations: ["Boston"],
    targetCompanies: ["Northwind"],
    excludedCompanies: ["Lumen"],
    keywords: ["kubernetes"],
    targetIndustries: ["SaaS"],
    excludedIndustries: ["Defense"],
    experienceGuidance: "Depth over breadth.",
  });
  b = addRequirement(b, { label: "Go", kind: "preferred", weight: 3 });
  return b;
}

describe("completeness", () => {
  it("is 0-ish for an empty draft", () => {
    const r = evaluateReadiness(newDraftReq(), newDraftBrief("r"));
    expect(r.completeness).toBeLessThan(20);
  });

  it("rises as sections are filled", () => {
    const empty = evaluateReadiness(newDraftReq(), newDraftBrief("r")).completeness;
    const partial = evaluateReadiness(baseReq(), newDraftBrief("r")).completeness;
    expect(partial).toBeGreaterThan(empty);
  });

  it("reaches 100 when everything is authored", () => {
    const req = updateReq(baseReq(), {
      hiringManager: "Dana",
      jobDescription: "x".repeat(400),
      intakeNotes: "Wants depth.",
      sourcingGoals: { targetCandidates: 20, targetContacts: 40, notes: "Two by month end." },
    });
    expect(evaluateReadiness(req, fullBrief()).completeness).toBe(100);
  });

  it("never exceeds 100 or drops below 0", () => {
    const req = updateReq(baseReq(), {
      hiringManager: "Dana",
      jobDescription: "x".repeat(5000),
      intakeNotes: "Notes",
      sourcingGoals: { targetCandidates: 999, targetContacts: 999, notes: "n" },
    });
    const pct = evaluateReadiness(req, fullBrief()).completeness;
    expect(pct).toBeGreaterThanOrEqual(0);
    expect(pct).toBeLessThanOrEqual(100);
  });

  it("reports every section", () => {
    const r = evaluateReadiness(baseReq(), fullBrief());
    expect(r.sections.map((s) => s.id)).toEqual([
      "role", "description", "intake", "targeting", "requirements", "keywords", "goals",
    ]);
  });

  it("bands the ring tone", () => {
    expect(completenessTone(10)).toBe("low");
    expect(completenessTone(50)).toBe("medium");
    expect(completenessTone(90)).toBe("high");
  });
});

describe("readiness is not completeness", () => {
  it("a highly complete req is NOT ready without a must-have", () => {
    const req = updateReq(baseReq(), {
      hiringManager: "Dana",
      jobDescription: "x".repeat(400),
      intakeNotes: "Notes",
      sourcingGoals: { targetCandidates: 20, targetContacts: 40, notes: "n" },
    });
    let brief = fullBrief();
    // Strip the only must-have.
    brief = reviseBrief(brief, {
      requirements: brief.requirements.filter((r) => r.kind !== "required"),
    });
    const r = evaluateReadiness(req, brief);
    expect(r.completeness).toBeGreaterThan(80);
    expect(r.sourcingReady).toBe(false);
    expect(r.blockers.join(" ")).toMatch(/must-have/i);
  });

  it("a modestly complete req CAN be ready", () => {
    const r = evaluateReadiness(baseReq(), fullBrief());
    expect(r.sourcingReady).toBe(true);
    expect(r.completeness).toBeLessThan(100);
  });

  it("is not ready when role basics are missing", () => {
    const r = evaluateReadiness(newDraftReq(), fullBrief());
    expect(r.sourcingReady).toBe(false);
    expect(r.blockers.length).toBeGreaterThan(0);
  });

  it("is not ready without a search headline", () => {
    const brief = reviseBrief(fullBrief(), { headline: "" });
    expect(evaluateReadiness(baseReq(), brief).sourcingReady).toBe(false);
  });

  it("handles a missing brief without crashing", () => {
    const r = evaluateReadiness(baseReq(), undefined);
    expect(r.sourcingReady).toBe(false);
    expect(r.completeness).toBeGreaterThanOrEqual(0);
  });

  it("separates blockers from suggestions", () => {
    const r = evaluateReadiness(baseReq(), fullBrief());
    expect(r.blockers).toEqual([]);           // essentials satisfied
    expect(r.suggestions.length).toBeGreaterThan(0);  // optional work remains
  });
});

describe("canOpenForSourcing", () => {
  it("allows a ready draft", () => {
    expect(canOpenForSourcing(baseReq(), fullBrief()).allowed).toBe(true);
  });

  it("refuses an unready draft and explains why", () => {
    const r = canOpenForSourcing(newDraftReq(), newDraftBrief("r"));
    expect(r.allowed).toBe(false);
    expect(r.reasons.length).toBeGreaterThan(0);
  });

  it("refuses an already-open req", () => {
    const open = transition(baseReq(), "open");
    const r = canOpenForSourcing(open, fullBrief());
    expect(r.allowed).toBe(false);
    expect(r.reasons[0]).toMatch(/already open/i);
  });

  it("refuses a closed req", () => {
    const closed = transition(baseReq(), "closed");
    expect(canOpenForSourcing(closed, fullBrief()).allowed).toBe(false);
  });
});
