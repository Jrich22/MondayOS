/**
 * The central product rule, under test:
 *
 *   Candidate    = persistent person across requisitions
 *   ReqCandidate = that person's evaluation and status for ONE requisition
 *
 * These tests are the guardrail. If someone later denormalizes a name onto the
 * pipeline row or lets one person hold a single global stage, they fail here.
 */
import { describe, it, expect, beforeEach } from "vitest";
import {
  IdentityDuplicationError,
  ReqCandidateStageError,
  advance,
  assess,
  assertNoIdentityDuplication,
  canAdvance,
  computeFitScore,
  isAlreadyOnReq,
  joinPipeline,
  needsReassessment,
  newReqCandidate,
  pipelineFor,
  reqHistoryFor,
  stageCounts,
  withFitScore,
} from "./req-candidate";
import { newCandidate } from "./candidate";
import { newBrief, reviseBrief } from "./brief";
import { __resetIdCounter } from "./ids";
import type { ReqCandidate, SourcingBrief } from "./types";

beforeEach(() => __resetIdCounter());

function brief(): SourcingBrief {
  return newBrief({
    reqId: "req_1",
    headline: "Platform engineers",
    seniority: "staff",
    requirements: [
      { label: "7+ years", kind: "required", weight: 5 },
      { label: "Kubernetes", kind: "required", weight: 4 },
      { label: "Go", kind: "preferred", weight: 3 },
      { label: "Mentorship", kind: "preferred", weight: 1 },
    ],
  });
}

function rc(reqId = "req_1", candidateId = "cand_1"): ReqCandidate {
  return newReqCandidate({ reqId, candidateId, briefVersion: 1, by: "Recruiter" });
}

describe("the Candidate / ReqCandidate separation", () => {
  it("holds only ids, never identity fields", () => {
    const row = rc();
    expect(row.candidateId).toBe("cand_1");
    expect(row.reqId).toBe("req_1");
    for (const field of ["fullName", "headline", "email", "linkedInUrl", "skills", "roles"]) {
      expect(row).not.toHaveProperty(field);
    }
  });

  it("rejects a row that duplicates candidate identity", () => {
    expect(() => assertNoIdentityDuplication({ ...rc(), fullName: "Priya Raman" })).toThrow(
      IdentityDuplicationError,
    );
  });

  it("names every leaked field in the error", () => {
    expect(() =>
      assertNoIdentityDuplication({ fullName: "x", email: "y@z.com", stage: "identified" }),
    ).toThrow(/fullName, email/);
  });

  it("accepts a clean row", () => {
    expect(() => assertNoIdentityDuplication(rc())).not.toThrow();
  });

  it("lets ONE person hold independent stages on TWO requisitions", () => {
    const person = newCandidate({ fullName: "Priya Raman", origin: "referral" });
    const onInfra = advance(rc("req_infra", person.id), "reviewing", "Dana");
    const onMl = advance(rc("req_ml", person.id), "rejected", "Marcus", "No ML serving");

    expect(onInfra.candidateId).toBe(onMl.candidateId);
    expect(onInfra.stage).toBe("reviewing");
    expect(onMl.stage).toBe("rejected");
  });

  it("gives one person different fit scores per requisition", () => {
    const b = brief();
    const strong = withFitScore(
      b.requirements.reduce((acc, r) => assess(acc, { requirementId: r.id, met: "yes", note: "" }), rc()),
      b,
    );
    const weak = withFitScore(
      assess(rc("req_2"), { requirementId: b.requirements[0].id, met: "no", note: "" }),
      b,
    );
    expect(strong.fitScore).toBe(100);
    expect(weak.fitScore).toBe(0);
  });

  it("collects a person's full cross-req history", () => {
    const all = [rc("req_a", "cand_1"), rc("req_b", "cand_1"), rc("req_a", "cand_2")];
    expect(reqHistoryFor("cand_1", all)).toHaveLength(2);
    expect(reqHistoryFor("cand_2", all)).toHaveLength(1);
  });

  it("detects a person already on a requisition", () => {
    const all = [rc("req_a", "cand_1")];
    expect(isAlreadyOnReq("cand_1", "req_a", all)).toBe(true);
    expect(isAlreadyOnReq("cand_1", "req_b", all)).toBe(false);
  });

  it("joins to candidates only for rendering", () => {
    const person = newCandidate({ fullName: "Priya Raman", origin: "referral" });
    const rows = joinPipeline([rc("req_1", person.id)], [person]);
    expect(rows[0].candidate.fullName).toBe("Priya Raman");
    expect(rows[0].reqCandidate).not.toHaveProperty("fullName");
  });

  it("drops rows whose person is missing rather than inventing one", () => {
    expect(joinPipeline([rc("req_1", "ghost")], [])).toHaveLength(0);
  });
});

describe("pipeline stages", () => {
  it("starts at identified with a history entry", () => {
    const row = rc();
    expect(row.stage).toBe("identified");
    expect(row.history).toHaveLength(1);
    expect(row.history[0].from).toBeNull();
  });

  it("permits the forward path", () => {
    expect(canAdvance("identified", "reviewing")).toBe(true);
    expect(canAdvance("reviewing", "contacted")).toBe(true);
    expect(canAdvance("responded", "advanced")).toBe(true);
  });

  it("refuses stage skips", () => {
    expect(canAdvance("identified", "advanced")).toBe(false);
    expect(() => advance(rc(), "advanced", "Recruiter")).toThrow(ReqCandidateStageError);
  });

  it("allows rejection from any active stage", () => {
    for (const stage of ["identified", "reviewing", "contacted", "responded"] as const) {
      expect(canAdvance(stage, "rejected")).toBe(true);
    }
  });

  it("allows a rejection to be revisited", () => {
    const rejected = advance(rc(), "rejected", "Recruiter");
    expect(() => advance(rejected, "reviewing", "Recruiter")).not.toThrow();
  });

  it("appends to history with attribution", () => {
    const moved = advance(rc(), "reviewing", "Dana", "Profile reviewed");
    expect(moved.history).toHaveLength(2);
    expect(moved.history[1]).toMatchObject({ from: "identified", to: "reviewing", by: "Dana" });
  });

  it("counts stages per requisition", () => {
    const all = [
      advance(rc("req_a", "c1"), "reviewing", "x"),
      rc("req_a", "c2"),
      rc("req_b", "c3"),
    ];
    const counts = stageCounts("req_a", all);
    expect(counts.reviewing).toBe(1);
    expect(counts.identified).toBe(1);
  });

  it("orders the pipeline by stage then fit", () => {
    const all = [rc("req_1", "c1"), advance(rc("req_1", "c2"), "reviewing", "x")];
    expect(pipelineFor("req_1", all)[0].candidateId).toBe("c2");
  });
});

describe("fit scoring", () => {
  it("returns 0 when any required item is unmet", () => {
    const b = brief();
    const row = assess(rc(), { requirementId: b.requirements[0].id, met: "no", note: "" });
    expect(computeFitScore(row, b)).toBe(0);
  });

  it("treats unknown on a required item as unmet", () => {
    const b = brief();
    let row = rc();
    row = assess(row, { requirementId: b.requirements[0].id, met: "unknown", note: "" });
    row = assess(row, { requirementId: b.requirements[1].id, met: "yes", note: "" });
    expect(computeFitScore(row, b)).toBe(0);
  });

  it("returns 100 when required met and nothing preferred judged", () => {
    const b = brief();
    let row = rc();
    row = assess(row, { requirementId: b.requirements[0].id, met: "yes", note: "" });
    row = assess(row, { requirementId: b.requirements[1].id, met: "yes", note: "" });
    expect(computeFitScore(row, b)).toBe(100);
  });

  it("weights preferred requirements", () => {
    const b = brief();
    let row = rc();
    row = assess(row, { requirementId: b.requirements[0].id, met: "yes", note: "" });
    row = assess(row, { requirementId: b.requirements[1].id, met: "yes", note: "" });
    row = assess(row, { requirementId: b.requirements[2].id, met: "yes", note: "" }); // weight 3
    row = assess(row, { requirementId: b.requirements[3].id, met: "no", note: "" }); // weight 1
    expect(computeFitScore(row, b)).toBe(75);
  });

  it("replaces rather than duplicates an assessment", () => {
    const b = brief();
    let row = assess(rc(), { requirementId: b.requirements[0].id, met: "no", note: "" });
    row = assess(row, { requirementId: b.requirements[0].id, met: "yes", note: "revised" });
    expect(row.assessments).toHaveLength(1);
    expect(row.assessments[0].met).toBe("yes");
  });
});

describe("brief versioning", () => {
  it("flags an evaluation made against an older brief", () => {
    const b = brief();
    const row = rc();
    expect(needsReassessment(row, b)).toBe(false);
    expect(needsReassessment(row, reviseBrief(b, { headline: "Changed" }))).toBe(true);
  });
});
