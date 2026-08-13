/**
 * Supervised session lifecycle: start, pause, resume, complete, skips, counts.
 *
 * The supervision boundary is the thing under test throughout. Every refusal
 * below is a case where continuing would produce a record implying human
 * oversight that did not happen.
 */
import { describe, it, expect, beforeEach } from "vitest";
import {
  SUPERVISION_POLICY,
  SupervisionRequiredError,
  activeSessionFor,
  aggregateCounts,
  closeCallsFor,
  completeSession,
  isActive,
  pauseSession,
  recordManualCapture,
  recordSkip,
  resumeSession,
  sessionCounts,
  sessionsForReq,
  startSession,
} from "./linkedin";
import { newCandidate } from "./candidate";
import { __resetIdCounter } from "./ids";

beforeEach(() => __resetIdCounter());

const OK = {
  reqId: "req_1",
  operator: "Dana Whitfield",
  acknowledgedPolicy: true,
  reqAcceptsSourcing: true,
};

const started = () => startSession(OK);
const person = (name = "Priya Raman") =>
  newCandidate({ fullName: name, origin: "supervised-linkedin" });

describe("starting a session", () => {
  it("initialises the Increment 3 fields", () => {
    const s = startSession({ ...OK, briefVersion: 3 });
    expect(s.status).toBe("in-progress");
    expect(s.briefVersion).toBe(3);
    expect(s.capturedCandidateIds).toEqual([]);
    expect(s.skipped).toEqual([]);
    expect(s.pauseCount).toBe(0);
  });

  it("still refuses without operator, acknowledgement, or an open req", () => {
    expect(() => startSession({ ...OK, operator: " " })).toThrow(SupervisionRequiredError);
    expect(() => startSession({ ...OK, acknowledgedPolicy: false })).toThrow(SupervisionRequiredError);
    expect(() => startSession({ ...OK, reqAcceptsSourcing: false })).toThrow(SupervisionRequiredError);
  });

  it("states a policy the operator must accept", () => {
    expect(SUPERVISION_POLICY.join(" ")).toMatch(/personally/i);
  });
});

describe("pause and resume", () => {
  it("pauses an in-progress session and counts the interruption", () => {
    const p = pauseSession(started());
    expect(p.status).toBe("paused");
    expect(p.pausedAt).toBeTruthy();
    expect(p.pauseCount).toBe(1);
  });

  it("treats a paused session as still active", () => {
    expect(isActive(pauseSession(started()))).toBe(true);
  });

  it("resumes a paused session", () => {
    const r = resumeSession(pauseSession(started()));
    expect(r.status).toBe("in-progress");
    expect(r.resumedAt).toBeTruthy();
  });

  it("counts repeated interruptions", () => {
    let s = started();
    for (let i = 0; i < 3; i += 1) s = resumeSession(pauseSession(s));
    expect(s.pauseCount).toBe(3);
  });

  it("refuses to pause a session that is not in progress", () => {
    expect(() => pauseSession(pauseSession(started()))).toThrow(/only an in-progress session/);
  });

  it("refuses to resume a session that is not paused", () => {
    expect(() => resumeSession(started())).toThrow(/only a paused session/);
  });

  it("refuses to resume without the policy acknowledgement", () => {
    const tampered = { ...pauseSession(started()), acknowledgedPolicy: false };
    expect(() => resumeSession(tampered)).toThrow(/acknowledgement/);
  });

  it("refuses capture while paused", () => {
    expect(() => recordManualCapture(pauseSession(started()), person())).toThrow(
      SupervisionRequiredError,
    );
  });

  it("refuses to record a skip while paused", () => {
    expect(() => recordSkip(pauseSession(started()), { name: "X", reason: "y" })).toThrow(
      SupervisionRequiredError,
    );
  });
});

describe("completing a session", () => {
  it("completes from in-progress", () => {
    const c = completeSession(started(), "Reviewed 12 profiles");
    expect(c.status).toBe("ended");
    expect(c.endedAt).toBeTruthy();
    expect(c.notes).toBe("Reviewed 12 profiles");
  });

  it("completes from paused — an interrupted session can still be closed out", () => {
    expect(completeSession(pauseSession(started())).status).toBe("ended");
  });

  it("is terminal", () => {
    const c = completeSession(started());
    expect(() => completeSession(c)).toThrow(SupervisionRequiredError);
    expect(() => resumeSession(c)).toThrow(SupervisionRequiredError);
    expect(isActive(c)).toBe(false);
  });
});

describe("capture attribution", () => {
  it("records the captured candidate id and increments the count", () => {
    const p = person();
    const s = recordManualCapture(started(), p);
    expect(s.capturedCandidateIds).toEqual([p.id]);
    expect(s.candidatesAdded).toBe(1);
  });

  it("still refuses a NEW candidate not marked as supervised capture", () => {
    // The laundering guard: a bulk import cannot be passed through a session to
    // look human-reviewed.
    const inbound = newCandidate({ fullName: "X", origin: "inbound" });
    expect(() => recordManualCapture(started(), inbound)).toThrow(/supervised-linkedin/);
  });

  it("permits reuse of an existing person only when declared explicitly", () => {
    // A referral sourced for a new req today WAS reviewed in this session, but
    // their origin correctly records how they first entered the pool. Rewriting
    // it to satisfy the guard would destroy real provenance.
    const referral = newCandidate({ fullName: "Priya Raman", origin: "referral" });
    expect(() => recordManualCapture(started(), referral)).toThrow(/supervised-linkedin/);
    const s = recordManualCapture(started(), referral, { reusedFromPool: true });
    expect(s.candidatesAdded).toBe(1);
    expect(s.capturedCandidateIds).toEqual([referral.id]);
  });

  it("reuse does not rewrite the person's origin", () => {
    const referral = newCandidate({ fullName: "Priya Raman", origin: "referral" });
    recordManualCapture(started(), referral, { reusedFromPool: true });
    expect(referral.origin).toBe("referral");
  });

  it("reuse is still refused when the session is not in progress", () => {
    const referral = newCandidate({ fullName: "X", origin: "referral" });
    expect(() =>
      recordManualCapture(pauseSession(started()), referral, { reusedFromPool: true }),
    ).toThrow(/not in progress/);
  });
});

describe("skipped and close-call tracking", () => {
  it("records a skip without creating a Candidate", () => {
    const s = recordSkip(started(), { name: "Tomás Beckett", reason: "Too junior" });
    expect(s.skipped).toHaveLength(1);
    expect(s.skipped?.[0]).toMatchObject({ name: "Tomás Beckett", closeCall: false });
    // No candidate record is minted — a skip is a judgement, not a person.
    expect(s.capturedCandidateIds).toEqual([]);
    expect(s.candidatesAdded).toBe(0);
  });

  it("flags a close call", () => {
    const s = recordSkip(started(), { name: "X", reason: "Borderline", closeCall: true });
    expect(s.skipped?.[0].closeCall).toBe(true);
  });

  it("refuses an unnamed skip — the record would mean nothing", () => {
    expect(() => recordSkip(started(), { name: "  ", reason: "x" })).toThrow(/needs a name/);
  });

  it("collects close calls across a req's sessions, newest first", () => {
    const a = recordSkip(started(), { name: "A", reason: "r", closeCall: true });
    const b = { ...recordSkip(started(), { name: "B", reason: "r", closeCall: true }),
                skipped: [{ id: "s2", name: "B", reason: "r", closeCall: true, at: "2099-01-01T00:00:00Z" }] };
    const plain = recordSkip(started(), { name: "C", reason: "r" });
    const calls = closeCallsFor("req_1", [a, b, plain]);
    expect(calls.map((c) => c.name)).toEqual(["B", "A"]);
  });
});

describe("session counts", () => {
  it("computes captured, skipped, close calls and capture rate", () => {
    let s = started();
    s = recordManualCapture(s, person("A"));
    s = recordManualCapture(s, person("B"));
    s = recordSkip(s, { name: "C", reason: "no", closeCall: true });
    s = recordSkip(s, { name: "D", reason: "no" });

    const c = sessionCounts(s);
    expect(c).toMatchObject({ captured: 2, skipped: 2, closeCalls: 1, reviewed: 4, captureRate: 50 });
  });

  it("reports a null capture rate before anyone is reviewed", () => {
    expect(sessionCounts(started()).captureRate).toBeNull();
  });

  it("falls back to candidatesAdded for sessions from an earlier increment", () => {
    const legacy = { ...started(), capturedCandidateIds: undefined, candidatesAdded: 4 };
    expect(sessionCounts(legacy).captured).toBe(4);
  });

  it("aggregates across sessions", () => {
    const a = recordManualCapture(started(), person("A"));
    const b = recordSkip(started(), { name: "B", reason: "no", closeCall: true });
    const total = aggregateCounts([a, b]);
    expect(total).toMatchObject({ captured: 1, skipped: 1, closeCalls: 1, reviewed: 2, captureRate: 50 });
  });

  it("aggregates to zero for no sessions", () => {
    expect(aggregateCounts([])).toMatchObject({ captured: 0, reviewed: 0, captureRate: null });
  });
});

describe("finding the active session", () => {
  it("returns an in-progress session", () => {
    expect(activeSessionFor("req_1", [started()])).not.toBeNull();
  });

  it("returns a paused session — it is still the operator's session", () => {
    expect(activeSessionFor("req_1", [pauseSession(started())])).not.toBeNull();
  });

  it("returns null once completed", () => {
    expect(activeSessionFor("req_1", [completeSession(started())])).toBeNull();
  });

  it("does not return another req's session", () => {
    expect(activeSessionFor("req_2", [started()])).toBeNull();
  });

  it("lists a req's sessions newest first", () => {
    const older = started();
    const newer = { ...started(), startedAt: "2099-01-01T00:00:00Z" };
    expect(sessionsForReq("req_1", [older, newer])[0].startedAt).toBe("2099-01-01T00:00:00Z");
  });
});

describe("demo seed data is unmistakably synthetic", () => {
  it("labels every seeded session in its own notes", async () => {
    const { seedState } = await import("./seed");
    const { sessions } = seedState();
    expect(sessions.length).toBeGreaterThan(0);
    for (const s of sessions) {
      expect(s.notes).toMatch(/^Demo data — synthetic session\./);
    }
  });

  it("seeds no LinkedIn URLs, so no capture is implied", async () => {
    const { seedState } = await import("./seed");
    expect(seedState().candidates.every((c) => !c.linkedInUrl)).toBe(true);
  });
});
