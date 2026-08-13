/**
 * @vitest-environment jsdom
 *
 * Authoring domain + persistence: drafts, edits, autosave state, and reopening.
 *
 * The invariant that matters across all of it: authoring extends the EXISTING
 * Req and SourcingBrief models. No competing authoring entity exists, and the
 * Candidate / ReqCandidate separation is untouched by any of this.
 */
import { describe, it, expect, beforeEach } from "vitest";
import {
  __resetStore,
  createDraftReq,
  getState,
  reqWorkspace,
  saveReqDraft,
  updateReq as storeUpdateReq,
} from "./store";
import {
  draftReqs,
  hasUnsavedChanges,
  markSaved,
  newDraftReq,
  newReq,
  suggestReqCode,
  transition,
  updateReq,
} from "./req";
import { addToList, newDraftBrief, removeFromList, reviseBrief } from "./brief";
import { evaluateReadiness } from "./readiness";
import { __resetIdCounter } from "./ids";

beforeEach(() => {
  localStorage.clear();
  __resetIdCounter();
  __resetStore();
});

describe("creating requisitions", () => {
  it("creates a draft req and its brief together", () => {
    const { req, brief } = createDraftReq();
    expect(req.status).toBe("draft");
    expect(brief.reqId).toBe(req.id);
    expect(getState().reqs).toHaveLength(1);
    expect(getState().briefs).toHaveLength(1);
  });

  it("supports multiple reqs side by side", () => {
    const a = createDraftReq();
    const b = createDraftReq();
    expect(a.req.id).not.toBe(b.req.id);
    expect(getState().reqs).toHaveLength(2);
    expect(getState().briefs).toHaveLength(2);
  });

  it("suggests sequential codes without reusing one", () => {
    expect(suggestReqCode([])).toBe("REQ-001");
    const first = createDraftReq().req;
    const second = createDraftReq().req;
    expect(first.code).toBe("REQ-001");
    expect(second.code).toBe("REQ-002");
  });

  it("continues from the highest existing code, ignoring non-matching ones", () => {
    const reqs = [
      newReq({ code: "REQ-014", title: "a", team: "t", location: "l" }),
      newReq({ code: "legacy-7", title: "b", team: "t", location: "l" }),
    ];
    expect(suggestReqCode(reqs)).toBe("REQ-015");
  });

  it("lists drafts separately", () => {
    createDraftReq();
    const open = transition(
      newReq({ code: "REQ-900", title: "x", team: "t", location: "l" }),
      "open",
    );
    expect(draftReqs([...getState().reqs, open])).toHaveLength(1);
  });
});

describe("editing requisitions", () => {
  it("applies edits and trims text", () => {
    const r = updateReq(newDraftReq(), { title: "  Staff Engineer  ", team: " Infra " });
    expect(r.title).toBe("Staff Engineer");
    expect(r.team).toBe("Infra");
  });

  it("floors openings at 1", () => {
    expect(updateReq(newDraftReq(), { openings: 0 }).openings).toBe(1);
  });

  it("stores the new authoring fields", () => {
    const r = updateReq(newDraftReq(), {
      jobDescription: "Full JD",
      intakeNotes: "HM wants depth",
      sourcingGoals: { targetCandidates: 20, notes: "two by month end" },
    });
    expect(r.jobDescription).toBe("Full JD");
    expect(r.intakeNotes).toBe("HM wants depth");
    expect(r.sourcingGoals?.targetCandidates).toBe(20);
  });

  it("cannot change status — lifecycle goes through transition()", () => {
    const r = updateReq(newDraftReq(), { title: "x" } as never);
    expect(r.status).toBe("draft");
    // @ts-expect-error status is deliberately outside ReqEdits
    updateReq(r, { status: "open" });
  });

  it("preserves id and createdAt", () => {
    const original = newDraftReq();
    const edited = updateReq(original, { title: "New" });
    expect(edited.id).toBe(original.id);
    expect(edited.createdAt).toBe(original.createdAt);
  });
});

describe("brief list editing", () => {
  it("adds, dedupes case-insensitively, and removes", () => {
    let b = newDraftBrief("r");
    b = addToList(b, "targetIndustries", "SaaS");
    b = addToList(b, "targetIndustries", "saas");
    expect(b.targetIndustries).toEqual(["SaaS"]);
    b = addToList(b, "excludedIndustries", "Defense");
    expect(b.excludedIndustries).toEqual(["Defense"]);
    b = removeFromList(b, "targetIndustries", "SaaS");
    expect(b.targetIndustries).toEqual([]);
  });

  it("ignores blank values", () => {
    const b = addToList(newDraftBrief("r"), "keywords", "   ");
    expect(b.keywords).toEqual([]);
  });

  it("bumps the brief version on every list change", () => {
    const b = newDraftBrief("r");
    expect(addToList(b, "keywords", "k8s").version).toBe(b.version + 1);
  });

  it("stores experience guidance", () => {
    const b = reviseBrief(newDraftBrief("r"), { experienceGuidance: "Depth over breadth." });
    expect(b.experienceGuidance).toBe("Depth over breadth.");
  });
});

describe("draft save state", () => {
  it("a brand-new draft has unsaved changes", () => {
    expect(hasUnsavedChanges(newDraftReq())).toBe(true);
  });

  it("markSaved clears the unsaved flag", () => {
    expect(hasUnsavedChanges(markSaved(newDraftReq()))).toBe(false);
  });

  it("editing after a save marks it unsaved again", () => {
    const saved = markSaved(newDraftReq());
    expect(hasUnsavedChanges(updateReq(saved, { title: "New" }))).toBe(true);
  });

  it("detects an edit made in the same millisecond as the save", () => {
    // Regression: this compared ISO timestamps, which resolve to the
    // millisecond, so a fast edit read as "all changes saved" while unwritten.
    const saved = markSaved(newDraftReq());
    const edited = updateReq(saved, { title: "Typed immediately" });
    expect(edited.updatedAt >= (saved.lastSavedAt ?? "")).toBe(true); // clocks may agree
    expect(hasUnsavedChanges(edited)).toBe(true); // revision does not
  });

  it("counts revisions monotonically across many rapid edits", () => {
    let r = newDraftReq();
    for (let i = 0; i < 50; i += 1) r = updateReq(r, { title: `t${i}` });
    expect(r.rev).toBe(50);
    expect(hasUnsavedChanges(markSaved(r))).toBe(false);
  });

  it("survives a reload — savedRev is persisted, not in-memory", () => {
    const { req, brief } = createDraftReq();
    saveReqDraft(updateReq(req, { title: "Persisted" }), brief);
    const raw = JSON.parse(localStorage.getItem("sourcingbot.workspace.v1") as string);
    expect(raw.reqs[0].savedRev).toBe(raw.reqs[0].rev);
    expect(hasUnsavedChanges(raw.reqs[0])).toBe(false);
  });

  it("saveReqDraft persists req and brief together and stamps the save", () => {
    const { req, brief } = createDraftReq();
    const edited = updateReq(req, { title: "Staff Engineer" });
    const editedBrief = reviseBrief(brief, { headline: "Platform engineers" });
    const saved = saveReqDraft(edited, editedBrief);

    expect(hasUnsavedChanges(saved)).toBe(false);
    expect(getState().reqs[0].title).toBe("Staff Engineer");
    expect(getState().briefs[0].headline).toBe("Platform engineers");
  });

  it("saveReqDraft adds a brief that is not yet in the store", () => {
    const req = newDraftReq();
    storeUpdateReq(req);
    __resetStore({ reqs: [req], briefs: [] });
    saveReqDraft(req, newDraftBrief(req.id));
    expect(getState().briefs).toHaveLength(1);
  });
});

describe("reopening a saved req", () => {
  it("round-trips through localStorage", () => {
    const { req, brief } = createDraftReq();
    saveReqDraft(
      updateReq(req, { title: "Staff Engineer", jobDescription: "JD body" }),
      reviseBrief(brief, { headline: "Platform engineers" }),
    );

    const raw = localStorage.getItem("sourcingbot.workspace.v1");
    expect(raw).toBeTruthy();
    const parsed = JSON.parse(raw as string);
    expect(parsed.reqs[0].title).toBe("Staff Engineer");
    expect(parsed.reqs[0].jobDescription).toBe("JD body");
    expect(parsed.briefs[0].headline).toBe("Platform engineers");
  });

  it("reqWorkspace returns the req with its brief", () => {
    const { req } = createDraftReq();
    const loaded = reqWorkspace(req.id);
    expect(loaded?.req.id).toBe(req.id);
    expect(loaded?.brief?.reqId).toBe(req.id);
  });

  it("reqWorkspace returns null for an unknown req", () => {
    expect(reqWorkspace("nope")).toBeNull();
  });

  it("readiness survives the round trip", () => {
    const { req, brief } = createDraftReq();
    const edited = updateReq(req, { title: "Staff Engineer", team: "Infra", location: "Boston" });
    saveReqDraft(edited, brief);
    const loaded = reqWorkspace(edited.id)!;
    expect(evaluateReadiness(loaded.req, loaded.brief).completeness).toBeGreaterThan(0);
  });
});

describe("the domain model is not duplicated", () => {
  it("authoring writes to reqs and briefs — no new collection appears", () => {
    createDraftReq();
    expect(Object.keys(getState()).sort()).toEqual([
      "briefs", "candidates", "reqCandidates", "reqs", "sessions",
    ]);
  });

  it("authoring never touches candidates or reqCandidates", () => {
    const { req, brief } = createDraftReq();
    saveReqDraft(updateReq(req, { title: "x" }), reviseBrief(brief, { headline: "y" }));
    expect(getState().candidates).toEqual([]);
    expect(getState().reqCandidates).toEqual([]);
  });
});
