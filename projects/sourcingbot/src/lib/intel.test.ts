/**
 * Workspace intelligence — derived views only.
 *
 * The property under test throughout: every value composes existing domain
 * functions and stores nothing, so it can never disagree with the records it
 * summarises.
 */
import { describe, it, expect, beforeEach } from "vitest";
import {
  SAVED_VIEWS,
  applyView,
  concentration,
  matchesDimension,
  poolRows,
  pulse,
  recentActivity,
  recommendedFocus,
  toCsv,
  type IntelInput,
} from "./intel";
import { newCandidate } from "./candidate";
import { newReq, transition } from "./req";
import { newBrief, reviseBrief } from "./brief";
import { advance, newReqCandidate } from "./req-candidate";
import { recordSkip, startSession } from "./linkedin";
import { __resetIdCounter } from "./ids";
import type { ReqCandidate } from "./types";

beforeEach(() => __resetIdCounter());

const EMPTY: IntelInput = { reqs: [], briefs: [], candidates: [], reqCandidates: [], sessions: [] };

function person(name: string, company = "Northwind", skills: string[] = ["Go"], location = "Boston") {
  return newCandidate({
    fullName: name,
    location,
    skills,
    roles: [{ title: "Staff Engineer", company, startedAt: "2022-01" }],
    origin: "supervised-linkedin",
  });
}

function openReq(code = "REQ-001") {
  return transition(newReq({ code, title: "Staff Engineer", team: "Infra", location: "Boston" }), "open");
}

describe("pulse", () => {
  it("is all zeros for an empty workspace", () => {
    expect(pulse(EMPTY)).toMatchObject({
      activeReqs: 0, activeSessions: 0, capturedToday: 0,
      closeCalls: 0, reusedCandidates: 0, needsReview: 0,
    });
  });

  it("counts open reqs only", () => {
    const input = { ...EMPTY, reqs: [openReq(), newReq({ code: "R2", title: "t", team: "t", location: "l" })] };
    expect(pulse(input).activeReqs).toBe(1);
  });

  it("counts live sessions, including paused ones", () => {
    const s = startSession({ reqId: "r", operator: "D", acknowledgedPolicy: true, reqAcceptsSourcing: true });
    expect(pulse({ ...EMPTY, sessions: [s] }).activeSessions).toBe(1);
  });

  it("counts candidates added today", () => {
    const rc = newReqCandidate({ reqId: "r", candidateId: "c", briefVersion: 1, by: "D" });
    expect(pulse({ ...EMPTY, reqCandidates: [rc] }).capturedToday).toBe(1);
    const old = { ...rc, addedAt: "2000-01-01T00:00:00.000Z" };
    expect(pulse({ ...EMPTY, reqCandidates: [old] }).capturedToday).toBe(0);
  });

  it("counts close calls across sessions", () => {
    let s = startSession({ reqId: "r", operator: "D", acknowledgedPolicy: true, reqAcceptsSourcing: true });
    s = recordSkip(s, { name: "A", reason: "x", closeCall: true });
    s = recordSkip(s, { name: "B", reason: "x" });
    expect(pulse({ ...EMPTY, sessions: [s] }).closeCalls).toBe(1);
  });

  it("counts people evaluated for more than one req", () => {
    const c = person("Priya");
    const input = {
      ...EMPTY,
      candidates: [c],
      reqCandidates: [
        newReqCandidate({ reqId: "r1", candidateId: c.id, briefVersion: 1, by: "D" }),
        newReqCandidate({ reqId: "r2", candidateId: c.id, briefVersion: 1, by: "D" }),
      ],
    };
    expect(pulse(input).reusedCandidates).toBe(1);
  });

  it("counts evaluations made against a superseded brief", () => {
    const req = openReq();
    const brief = reviseBrief(newBrief({ reqId: req.id, headline: "h", seniority: "staff" }), { headline: "h2" });
    const rc = newReqCandidate({ reqId: req.id, candidateId: "c", briefVersion: 1, by: "D" });
    expect(pulse({ ...EMPTY, reqs: [req], briefs: [brief], reqCandidates: [rc] }).needsReview).toBe(1);
  });
});

describe("recommended focus", () => {
  it("is empty for an empty workspace", () => {
    expect(recommendedFocus(EMPTY)).toEqual([]);
  });

  it("surfaces a strong candidate nobody has acted on", () => {
    const req = openReq();
    const c = person("Priya");
    const rc: ReqCandidate = { ...newReqCandidate({ reqId: req.id, candidateId: c.id, briefVersion: 1, by: "D" }), fitScore: 88 };
    const items = recommendedFocus({ ...EMPTY, reqs: [req], candidates: [c], reqCandidates: [rc] });
    const strong = items.find((i) => i.kind === "strong-candidate");
    expect(strong?.title).toBe("Priya");
    expect(strong?.reason).toMatch(/88% fit/);
    expect(strong?.href).toBe(`/reqs/${req.id}`);
  });

  it("does not surface a strong candidate already advanced", () => {
    const req = openReq();
    const c = person("Priya");
    let rc: ReqCandidate = { ...newReqCandidate({ reqId: req.id, candidateId: c.id, briefVersion: 1, by: "D" }), fitScore: 88 };
    rc = advance(rc, "reviewing", "D");
    rc = advance(rc, "contacted", "D");
    const items = recommendedFocus({ ...EMPTY, reqs: [req], candidates: [c], reqCandidates: [rc] });
    expect(items.find((i) => i.kind === "strong-candidate")).toBeUndefined();
  });

  it("surfaces close calls with the req they came from", () => {
    const req = openReq();
    const s = recordSkip(
      startSession({ reqId: req.id, operator: "D", acknowledgedPolicy: true, reqAcceptsSourcing: true }),
      { name: "Tomás", reason: "Too junior", closeCall: true },
    );
    const item = recommendedFocus({ ...EMPTY, reqs: [req], sessions: [s] }).find((i) => i.kind === "close-call");
    expect(item?.title).toBe("Tomás");
    expect(item?.reason).toMatch(/REQ-001/);
    expect(item?.href).toBe(`/reqs/${req.id}/session`);
  });

  it("surfaces an open req with nobody in the pipeline", () => {
    const req = openReq();
    const item = recommendedFocus({ ...EMPTY, reqs: [req] }).find((i) => i.kind === "thin-pipeline");
    expect(item?.reason).toMatch(/nobody in the pipeline/);
    expect(item?.actionLabel).toBe("Start sourcing");
  });

  it("does not flag a healthy pipeline", () => {
    const req = openReq();
    const rcs = ["a", "b", "c"].map((id) =>
      newReqCandidate({ reqId: req.id, candidateId: id, briefVersion: 1, by: "D" }),
    );
    const items = recommendedFocus({ ...EMPTY, reqs: [req], reqCandidates: rcs });
    expect(items.find((i) => i.kind === "thin-pipeline")).toBeUndefined();
  });

  it("surfaces a low-capture session and points at the brief, not the operator", () => {
    const req = openReq();
    let s = startSession({ reqId: req.id, operator: "D", acknowledgedPolicy: true, reqAcceptsSourcing: true });
    for (const n of ["a", "b", "c", "d", "e", "f"]) s = recordSkip(s, { name: n, reason: "no" });
    const item = recommendedFocus({ ...EMPTY, reqs: [req], sessions: [s] }).find((i) => i.kind === "weak-session");
    // Neutral about cause: a low rate can be the brief, the criteria, the
    // approach — or a genuinely thin market where nothing is wrong.
    expect(item?.reason).toMatch(/Review the brief, search criteria, or sourcing approach/);
    expect(item?.reason).not.toMatch(/too narrow|brief is|operator/i);
    expect(item?.href).toBe(`/reqs/${req.id}/edit`);
  });

  it("ignores a short session — too little signal to judge", () => {
    const req = openReq();
    const s = recordSkip(
      startSession({ reqId: req.id, operator: "D", acknowledgedPolicy: true, reqAcceptsSourcing: true }),
      { name: "a", reason: "no" },
    );
    expect(recommendedFocus({ ...EMPTY, reqs: [req], sessions: [s] }).find((i) => i.kind === "weak-session")).toBeUndefined();
  });

  it("surfaces a duplicate pair only once", () => {
    const a = newCandidate({ fullName: "Priya Raman", email: "p@x.com", origin: "referral" });
    const b = newCandidate({ fullName: "P. Raman", email: "P@X.com", origin: "inbound" });
    const dups = recommendedFocus({ ...EMPTY, candidates: [a, b] }).filter((i) => i.kind === "reuse-opportunity");
    expect(dups).toHaveLength(1);
  });

  it("surfaces a stale evaluation", () => {
    const req = openReq();
    const brief = reviseBrief(newBrief({ reqId: req.id, headline: "h", seniority: "staff" }), { headline: "h2" });
    const c = person("Priya");
    const rc = newReqCandidate({ reqId: req.id, candidateId: c.id, briefVersion: 1, by: "D" });
    const item = recommendedFocus({ ...EMPTY, reqs: [req], briefs: [brief], candidates: [c], reqCandidates: [rc] })
      .find((i) => i.kind === "stale-evaluation");
    expect(item?.reason).toMatch(/v1, now v2/);
  });

  it("ranks by priority and respects the limit", () => {
    const req = openReq();
    const c = person("Priya");
    const rc: ReqCandidate = { ...newReqCandidate({ reqId: req.id, candidateId: c.id, briefVersion: 1, by: "D" }), fitScore: 95 };
    const items = recommendedFocus({ ...EMPTY, reqs: [req], candidates: [c], reqCandidates: [rc] }, 2);
    expect(items).toHaveLength(2);
    expect(items[0].priority).toBeGreaterThanOrEqual(items[1].priority);
    expect(items[0].kind).toBe("strong-candidate"); // outranks thin-pipeline
  });

  it("every item carries a reason and an action", () => {
    const req = openReq();
    const c = person("Priya");
    const rc: ReqCandidate = { ...newReqCandidate({ reqId: req.id, candidateId: c.id, briefVersion: 1, by: "D" }), fitScore: 90 };
    for (const i of recommendedFocus({ ...EMPTY, reqs: [req], candidates: [c], reqCandidates: [rc] })) {
      expect(i.reason.length).toBeGreaterThan(0);
      expect(i.actionLabel.length).toBeGreaterThan(0);
      expect(i.href.startsWith("/")).toBe(true);
    }
  });
});

describe("concentration", () => {
  const pool = [
    person("A", "Northwind", ["Go", "Kubernetes"], "Boston"),
    person("B", "Northwind", ["Go"], "Boston"),
    person("C", "Helix", ["Rust"], "Remote"),
  ];

  it("delegates company concentration to the existing function", () => {
    const rows = concentration(pool, "company");
    expect(rows[0]).toMatchObject({ label: "Northwind", count: 2, share: 67 });
  });

  it("computes location, title and skill", () => {
    expect(concentration(pool, "location")[0]).toMatchObject({ label: "Boston", count: 2 });
    expect(concentration(pool, "title")[0]).toMatchObject({ label: "Staff Engineer", count: 3 });
    expect(concentration(pool, "skill")[0]).toMatchObject({ label: "Go", count: 2 });
  });

  it("returns nothing for an empty pool", () => {
    expect(concentration([], "company")).toEqual([]);
  });

  it("respects the limit", () => {
    expect(concentration(pool, "skill", 1)).toHaveLength(1);
  });

  it("matchesDimension powers click-to-filter, case-insensitively", () => {
    expect(matchesDimension(pool[0], "company", "northwind")).toBe(true);
    expect(matchesDimension(pool[0], "skill", "GO")).toBe(true);
    expect(matchesDimension(pool[0], "location", "Remote")).toBe(false);
  });
});

describe("recent activity", () => {
  it("merges captures, stage moves and skips, newest first", () => {
    const req = openReq();
    const c = person("Priya");
    const rc = advance(
      newReqCandidate({ reqId: req.id, candidateId: c.id, briefVersion: 1, by: "D" }),
      "reviewing", "D", "Looks strong",
    );
    const s = recordSkip(
      startSession({ reqId: req.id, operator: "D", acknowledgedPolicy: true, reqAcceptsSourcing: true }),
      { name: "Tomás", reason: "junior", closeCall: true },
    );
    const feed = recentActivity({ ...EMPTY, reqs: [req], candidates: [c], reqCandidates: [rc], sessions: [s] });

    expect(feed.map((f) => f.kind).sort()).toEqual(["capture", "evaluation", "skip"]);
    const times = feed.map((f) => f.at);
    expect([...times].sort().reverse()).toEqual(times);
  });

  it("skips rows whose person or req is missing rather than inventing one", () => {
    const rc = newReqCandidate({ reqId: "ghost", candidateId: "ghost", briefVersion: 1, by: "D" });
    expect(recentActivity({ ...EMPTY, reqCandidates: [rc] })).toEqual([]);
  });

  it("respects the limit", () => {
    const req = openReq();
    const cands = ["a", "b", "c"].map((n) => person(n));
    const rcs = cands.map((c) => newReqCandidate({ reqId: req.id, candidateId: c.id, briefVersion: 1, by: "D" }));
    expect(recentActivity({ ...EMPTY, reqs: [req], candidates: cands, reqCandidates: rcs }, 2)).toHaveLength(2);
  });
});

describe("talent pool rows", () => {
  const req = openReq();
  const c = person("Priya");
  const rows = () =>
    poolRows({
      ...EMPTY,
      reqs: [req],
      candidates: [c],
      reqCandidates: [
        { ...newReqCandidate({ reqId: "r1", candidateId: c.id, briefVersion: 1, by: "D" }), fitScore: 40 } as ReqCandidate,
        { ...newReqCandidate({ reqId: "r2", candidateId: c.id, briefVersion: 1, by: "D" }), fitScore: 90 } as ReqCandidate,
      ],
    });

  it("enriches with company, title, req count and best fit", () => {
    expect(rows()[0]).toMatchObject({ company: "Northwind", title: "Staff Engineer", reqCount: 2, bestFit: 90 });
  });

  it("reports null fit when nothing was scored", () => {
    const plain = poolRows({ ...EMPTY, candidates: [c] });
    expect(plain[0]).toMatchObject({ reqCount: 0, bestFit: null });
  });

  it("saved views filter as named", () => {
    const r = rows();
    expect(applyView(r, "multi-req")).toHaveLength(1);
    expect(applyView(r, "scored")).toHaveLength(1);
    expect(applyView(r, "unevaluated")).toHaveLength(0);
    expect(applyView(r, "supervised")).toHaveLength(1);
    expect(applyView(r, "all")).toHaveLength(1);
  });

  it("exposes every saved view with a label and hint", () => {
    for (const v of SAVED_VIEWS) {
      expect(v.label.length).toBeGreaterThan(0);
      expect(v.hint.length).toBeGreaterThan(0);
    }
  });
});

describe("CSV export", () => {
  it("emits a header and one row per person", () => {
    const c = person("Priya");
    const csv = toCsv(poolRows({ ...EMPTY, candidates: [c] }));
    const lines = csv.split("\n");
    expect(lines).toHaveLength(2);
    expect(lines[0]).toMatch(/^"Name","Headline"/);
    expect(lines[1]).toMatch(/"Priya"/);
  });

  it("quotes fields so commas and quotes cannot corrupt the file", () => {
    const c = newCandidate({ fullName: 'Raman, "Priya"', origin: "referral" });
    const csv = toCsv(poolRows({ ...EMPTY, candidates: [c] }));
    expect(csv).toContain('"Raman, ""Priya"""');
    expect(csv.split("\n")).toHaveLength(2); // the comma did not split the row
  });

  it("emits just the header for an empty pool", () => {
    expect(toCsv([]).split("\n")).toHaveLength(1);
  });
});
