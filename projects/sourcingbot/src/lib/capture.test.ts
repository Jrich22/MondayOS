/**
 * @vitest-environment jsdom
 *
 * Manual capture: duplicate detection, Candidate reuse, ReqCandidate creation,
 * and the atomic commit.
 *
 * The invariant under test throughout is the product's defining one: a capture
 * creates an EVALUATION for this req, and reuses the PERSON if they already
 * exist. Sourcing the same human for a second req must never mint a second
 * human.
 */
import { describe, it, expect, beforeEach } from "vitest";
import {
  CaptureError,
  captureCandidate,
  draftCandidateFrom,
  findDuplicatesFor,
  type CaptureContext,
  type CaptureInput,
} from "./capture";
import { SupervisionRequiredError, pauseSession, startSession } from "./sourcing-session";
import { newCandidate } from "./candidate";
import { newBrief } from "./brief";
import { reqHistoryFor } from "./req-candidate";
import { __resetStore, addCandidate, addSession, commitCapture, getState } from "./store";
import { __resetIdCounter } from "./ids";
import type { SourcingBrief } from "./types";

beforeEach(() => {
  localStorage.clear();
  __resetIdCounter();
  __resetStore();
});

const brief = (): SourcingBrief =>
  newBrief({
    reqId: "req_1",
    headline: "Platform engineers",
    seniority: "staff",
    requirements: [
      { label: "7+ years", kind: "required", weight: 5 },
      { label: "Go", kind: "preferred", weight: 3 },
    ],
  });

const session = () =>
  startSession({
    reqId: "req_1",
    operator: "Dana",
    acknowledgedPolicy: true,
    reqAcceptsSourcing: true,
  });

const input = (over: Partial<CaptureInput> = {}): CaptureInput => ({
  fullName: "Priya Raman",
  headline: "Staff Infrastructure Engineer",
  currentTitle: "Staff Engineer",
  currentCompany: "Northwind Cloud",
  rationale: "Closest match on multi-tenant ownership",
  ...over,
});

const ctx = (over: Partial<CaptureContext> = {}): CaptureContext => ({
  session: session(),
  reqId: "req_1",
  brief: brief(),
  candidates: [],
  reqCandidates: [],
  operator: "Dana",
  ...over,
});

describe("drafting a candidate from operator input", () => {
  it("marks the person as supervised capture", () => {
    expect(draftCandidateFrom(input()).origin).toBe("supervised-session");
  });

  it("builds a current role from title and company", () => {
    const c = draftCandidateFrom(input());
    expect(c.roles[0]).toMatchObject({ title: "Staff Engineer", company: "Northwind Cloud" });
    // Month precision only — no scraped precision implied.
    expect(c.roles[0].startedAt).toMatch(/^\d{4}-\d{2}$/);
  });

  it("creates no role when neither title nor company is given", () => {
    expect(draftCandidateFrom({ fullName: "X" }).roles).toEqual([]);
  });
});

describe("duplicate detection", () => {
  it("finds an existing person by email", () => {
    const existing = newCandidate({
      fullName: "P. Raman", email: "p@example.com", origin: "referral",
    });
    expect(findDuplicatesFor(input({ email: "P@Example.com" }), [existing])).toHaveLength(1);
  });

  it("finds an existing person by name and current company", () => {
    const existing = newCandidate({
      fullName: "priya  raman",
      roles: [{ title: "Eng", company: "Northwind Cloud", startedAt: "2022-01" }],
      origin: "referral",
    });
    expect(findDuplicatesFor(input(), [existing])).toHaveLength(1);
  });

  it("does not flag the same name at a different company", () => {
    const other = newCandidate({
      fullName: "Priya Raman",
      roles: [{ title: "Eng", company: "Helix", startedAt: "2022-01" }],
      origin: "referral",
    });
    expect(findDuplicatesFor(input(), [other])).toHaveLength(0);
  });

  it("is advisory — capture proceeds as new unless the operator reuses", () => {
    const existing = newCandidate({
      fullName: "Priya Raman", email: "p@example.com", origin: "referral",
    });
    const r = captureCandidate(ctx({ candidates: [existing] }), input({ email: "p@example.com" }));
    expect(r.reusedExistingCandidate).toBe(false);
    expect(r.candidate.id).not.toBe(existing.id);
  });
});

describe("capturing a candidate", () => {
  it("creates a person and an evaluation for the req", () => {
    const r = captureCandidate(ctx(), input());
    expect(r.candidate.fullName).toBe("Priya Raman");
    expect(r.reqCandidate.reqId).toBe("req_1");
    expect(r.reqCandidate.candidateId).toBe(r.candidate.id);
    expect(r.reqCandidate.stage).toBe("identified");
  });

  it("puts req-scoped rationale on the evaluation, not the person", () => {
    const r = captureCandidate(ctx(), input({ rationale: "Best multi-tenant depth" }));
    expect(r.reqCandidate.rationale).toBe("Best multi-tenant depth");
    expect(r.candidate).not.toHaveProperty("rationale");
  });

  it("puts durable notes on the person, not the evaluation", () => {
    const r = captureCandidate(ctx(), input({ personNotes: "Prefers Boston" }));
    expect(r.candidate.notes).toBe("Prefers Boston");
  });

  it("records the brief version the evaluation was made against", () => {
    const b = brief();
    const r = captureCandidate(ctx({ brief: b }), input());
    expect(r.reqCandidate.briefVersion).toBe(b.version);
  });

  it("applies assessments and computes fit", () => {
    const b = brief();
    const r = captureCandidate(
      ctx({ brief: b }),
      input({
        assessments: [
          { requirementId: b.requirements[0].id, met: "yes" },
          { requirementId: b.requirements[1].id, met: "yes" },
        ],
      }),
    );
    expect(r.reqCandidate.assessments).toHaveLength(2);
    expect(r.reqCandidate.fitScore).toBe(100);
  });

  it("leaves fit unscored when no assessment was made", () => {
    expect(captureCandidate(ctx(), input()).reqCandidate.fitScore).toBeNull();
  });

  it("attributes the capture to the session", () => {
    const r = captureCandidate(ctx(), input());
    expect(r.session.candidatesAdded).toBe(1);
    expect(r.session.capturedCandidateIds).toEqual([r.candidate.id]);
  });

  it("refuses an unnamed candidate", () => {
    expect(() => captureCandidate(ctx(), input({ fullName: "  " }))).toThrow(CaptureError);
  });

  it("refuses when the session is not in progress", () => {
    expect(() => captureCandidate(ctx({ session: pauseSession(session()) }), input())).toThrow(
      SupervisionRequiredError,
    );
  });
});

describe("reusing an existing person", () => {
  it("adds an evaluation without creating a second person", () => {
    const existing = newCandidate({ fullName: "Priya Raman", origin: "referral" });
    const r = captureCandidate(ctx({ candidates: [existing] }), input(), {
      kind: "existing",
      candidateId: existing.id,
    });
    expect(r.reusedExistingCandidate).toBe(true);
    expect(r.candidate.id).toBe(existing.id);
    expect(r.reqCandidate.candidateId).toBe(existing.id);
  });

  it("does not overwrite the existing person's record", () => {
    const existing = newCandidate({
      fullName: "Priya Raman", notes: "Original note", origin: "referral",
    });
    const r = captureCandidate(ctx({ candidates: [existing] }), input({ personNotes: "New note" }), {
      kind: "existing",
      candidateId: existing.id,
    });
    // Facts captured in an earlier search are not clobbered by a hastier later one.
    expect(r.candidate.notes).toBe("Original note");
    expect(r.candidate.origin).toBe("referral");
  });

  it("refuses to reuse someone no longer in the pool", () => {
    expect(() =>
      captureCandidate(ctx(), input(), { kind: "existing", candidateId: "gone" }),
    ).toThrow(/no longer in the talent pool/);
  });

  it("refuses to add the same person to the same req twice", () => {
    const existing = newCandidate({ fullName: "Priya Raman", origin: "referral" });
    const first = captureCandidate(ctx({ candidates: [existing] }), input(), {
      kind: "existing",
      candidateId: existing.id,
    });
    expect(() =>
      captureCandidate(
        ctx({ candidates: [existing], reqCandidates: [first.reqCandidate] }),
        input(),
        { kind: "existing", candidateId: existing.id },
      ),
    ).toThrow(/already on this requisition/);
  });

  it("ALLOWS the same person on a DIFFERENT req — the whole point of the pool", () => {
    const existing = newCandidate({ fullName: "Priya Raman", origin: "referral" });
    const onReq1 = captureCandidate(ctx({ candidates: [existing] }), input(), {
      kind: "existing",
      candidateId: existing.id,
    });
    const onReq2 = captureCandidate(
      ctx({
        candidates: [existing],
        reqCandidates: [onReq1.reqCandidate],
        reqId: "req_2",
        session: { ...session(), reqId: "req_2" },
      }),
      input(),
      { kind: "existing", candidateId: existing.id },
    );
    expect(onReq2.reqCandidate.candidateId).toBe(existing.id);
    expect(onReq2.reqCandidate.reqId).toBe("req_2");
    expect(reqHistoryFor(existing.id, [onReq1.reqCandidate, onReq2.reqCandidate])).toHaveLength(2);
  });
});

describe("committing a capture", () => {
  it("writes person, evaluation and session in one update", () => {
    const s = session();
    addSession(s);
    const r = captureCandidate(ctx({ session: s }), input());
    commitCapture(r);

    const state = getState();
    expect(state.candidates).toHaveLength(1);
    expect(state.reqCandidates).toHaveLength(1);
    expect(state.sessions[0].candidatesAdded).toBe(1);
  });

  it("does not duplicate the person when reusing", () => {
    const existing = newCandidate({ fullName: "Priya Raman", origin: "referral" });
    addCandidate(existing);
    const s = session();
    addSession(s);

    const r = captureCandidate(ctx({ session: s, candidates: [existing] }), input(), {
      kind: "existing",
      candidateId: existing.id,
    });
    commitCapture(r);

    expect(getState().candidates).toHaveLength(1);
    expect(getState().reqCandidates).toHaveLength(1);
  });

  it("persists through localStorage", () => {
    const s = session();
    addSession(s);
    commitCapture(captureCandidate(ctx({ session: s }), input()));

    const raw = JSON.parse(localStorage.getItem("sourcingbot.workspace.v1") as string);
    expect(raw.candidates).toHaveLength(1);
    expect(raw.reqCandidates[0].candidateId).toBe(raw.candidates[0].id);
    // The evaluation stores an id, never the person's name.
    expect(JSON.stringify(raw.reqCandidates)).not.toContain("Priya Raman");
  });
});
