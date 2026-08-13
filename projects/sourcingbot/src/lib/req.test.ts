import { describe, it, expect, beforeEach } from "vitest";
import {
  ReqTransitionError,
  acceptsSourcing,
  canTransition,
  newReq,
  sortForWorkspace,
  transition,
  validateReq,
} from "./req";
import {
  addRequirement,
  setRequirementKind,
  setRequirementWeight,
  briefReadinessIssues,
  clampWeight,
  isSourcingReady,
  isStaleAgainst,
  newBrief,
  removeRequirement,
  requiredRequirements,
  reviseBrief,
} from "./brief";
import { __resetIdCounter } from "./ids";

beforeEach(() => __resetIdCounter());

const base = { code: "REQ-014", title: "Staff Platform Engineer", team: "Infra", location: "Boston" };

describe("Req lifecycle", () => {
  it("starts as a draft with sane defaults", () => {
    const r = newReq(base);
    expect(r.status).toBe("draft");
    expect(r.openings).toBe(1);
    expect(r.workModel).toBe("hybrid");
  });

  it("floors openings at 1", () => {
    expect(newReq({ ...base, openings: 0 }).openings).toBe(1);
  });

  it("permits draft → open → on-hold → closed", () => {
    expect(canTransition("draft", "open")).toBe(true);
    expect(canTransition("open", "on-hold")).toBe(true);
    expect(canTransition("on-hold", "closed")).toBe(true);
  });

  it("treats closed as terminal", () => {
    expect(canTransition("closed", "open")).toBe(false);
    const closed = transition(newReq(base), "closed");
    expect(() => transition(closed, "open")).toThrow(ReqTransitionError);
  });

  it("refuses draft → on-hold", () => {
    expect(() => transition(newReq(base), "on-hold")).toThrow(ReqTransitionError);
  });

  it("stamps closedAt only on close", () => {
    const opened = transition(newReq(base), "open");
    expect(opened.closedAt).toBeUndefined();
    expect(transition(opened, "closed").closedAt).toBeTruthy();
  });

  it("accepts sourcing only while open", () => {
    const draft = newReq(base);
    expect(acceptsSourcing(draft)).toBe(false);
    expect(acceptsSourcing(transition(draft, "open"))).toBe(true);
  });

  it("validates required fields", () => {
    const issues = validateReq({ code: " ", title: "", team: "", location: "x" });
    expect(issues.map((i) => i.field)).toEqual(["code", "title", "team"]);
    expect(validateReq(base)).toEqual([]);
  });

  it("orders open work first in the workspace", () => {
    const draft = newReq(base);
    const open = transition(newReq({ ...base, code: "REQ-2" }), "open");
    const closed = transition(newReq({ ...base, code: "REQ-3" }), "closed");
    expect(sortForWorkspace([closed, draft, open]).map((r) => r.status)).toEqual([
      "open",
      "draft",
      "closed",
    ]);
  });
});

describe("Sourcing Brief", () => {
  const make = () =>
    newBrief({
      reqId: "req_1",
      headline: "Platform engineers",
      seniority: "staff",
      requirements: [{ label: "Kubernetes", kind: "required", weight: 4 }],
      keywords: ["k8s", "K8S", " platform "],
    });

  it("starts at version 1 and dedupes list fields case-insensitively", () => {
    const b = make();
    expect(b.version).toBe(1);
    expect(b.keywords).toEqual(["k8s", "platform"]);
  });

  it("clamps requirement weight to 1–5", () => {
    expect(clampWeight(0)).toBe(1);
    expect(clampWeight(9)).toBe(5);
    expect(clampWeight(Number.NaN)).toBe(1);
    expect(clampWeight(3.4)).toBe(3);
  });

  it("bumps the version on every revision", () => {
    const b = make();
    expect(reviseBrief(b, { headline: "New" }).version).toBe(2);
    expect(addRequirement(b, { label: "Go", kind: "preferred", weight: 3 }).version).toBe(2);
  });

  it("removes a requirement and bumps the version", () => {
    const b = make();
    const removed = removeRequirement(b, b.requirements[0].id);
    expect(removed.requirements).toHaveLength(0);
    expect(removed.version).toBe(2);
  });

  it("flags an older evaluation as stale", () => {
    const b = reviseBrief(make(), { headline: "New" });
    expect(isStaleAgainst(b, 1)).toBe(true);
    expect(isStaleAgainst(b, 2)).toBe(false);
  });

  it("is sourcing-ready only with a headline and a required requirement", () => {
    expect(isSourcingReady(make())).toBe(true);

    const noRequired = newBrief({
      reqId: "r",
      headline: "Search",
      seniority: "mid",
      requirements: [{ label: "Nice", kind: "preferred", weight: 2 }],
    });
    expect(isSourcingReady(noRequired)).toBe(false);
    expect(briefReadinessIssues(noRequired)).toHaveLength(1);

    const bare = newBrief({ reqId: "r", headline: "  ", seniority: "mid" });
    expect(briefReadinessIssues(bare)).toHaveLength(2);
  });

  it("separates required from preferred", () => {
    const b = addRequirement(make(), { label: "Go", kind: "preferred", weight: 3 });
    expect(requiredRequirements(b)).toHaveLength(1);
    expect(b.requirements).toHaveLength(2);
  });
});

describe("editable requirement weights (Increment 3)", () => {
  const withReqs = () =>
    addRequirement(
      newBrief({
        reqId: "r", headline: "Search", seniority: "staff",
        requirements: [{ label: "Kubernetes", kind: "required", weight: 4 }],
      }),
      { label: "Go", kind: "preferred", weight: 3 },
    );

  it("changes a weight and bumps the brief version", () => {
    const b = withReqs();
    const updated = setRequirementWeight(b, b.requirements[1].id, 5);
    expect(updated.requirements[1].weight).toBe(5);
    expect(updated.version).toBe(b.version + 1);
  });

  it("clamps out-of-range weights", () => {
    const b = withReqs();
    expect(setRequirementWeight(b, b.requirements[0].id, 99).requirements[0].weight).toBe(5);
    expect(setRequirementWeight(b, b.requirements[0].id, -3).requirements[0].weight).toBe(1);
  });

  it("is a no-op when the weight is unchanged — no spurious version bump", () => {
    const b = withReqs();
    expect(setRequirementWeight(b, b.requirements[0].id, 4)).toBe(b);
  });

  it("is a no-op for an unknown requirement", () => {
    const b = withReqs();
    expect(setRequirementWeight(b, "nope", 3)).toBe(b);
  });

  it("moves a requirement between must-have and nice-to-have", () => {
    const b = withReqs();
    const moved = setRequirementKind(b, b.requirements[0].id, "preferred");
    expect(moved.requirements[0].kind).toBe("preferred");
    expect(requiredRequirements(moved)).toHaveLength(0);
    expect(moved.version).toBe(b.version + 1);
  });

  it("changing kind is a no-op when already that kind", () => {
    const b = withReqs();
    expect(setRequirementKind(b, b.requirements[0].id, "required")).toBe(b);
  });

  it("a weight change flags existing evaluations for reassessment", () => {
    // Version bump is what makes needsReassessment() fire — evaluations made
    // against the old weighting are not silently rescored.
    const b = withReqs();
    const updated = setRequirementWeight(b, b.requirements[1].id, 5);
    expect(isStaleAgainst(updated, b.version)).toBe(true);
  });
});
