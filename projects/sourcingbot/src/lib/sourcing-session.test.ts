/**
 * The LinkedIn supervision boundary, under test.
 *
 * These assertions exist so the boundary cannot erode quietly: a future change
 * that lets a session start without a named operator, without an explicit
 * acknowledgement, or that claims an automation capability, fails here.
 */
import { describe, it, expect, beforeEach } from "vitest";
import {
  PROHIBITED_CAPABILITIES,
  SupervisionRequiredError,
  completeSession,
  endSession,
  recordManualCapture,
  sessionsForReq,
  startSession,
  supportsCapability,
} from "./sourcing-session";
import { ManualProvider } from "./providers/manual";
import { newCandidate } from "./candidate";
import { __resetIdCounter } from "./ids";

beforeEach(() => __resetIdCounter());

const OK = {
  reqId: "req_1",
  operator: "Dana Whitfield",
  acknowledgedPolicy: true,
  reqAcceptsSourcing: true,
};

describe("supervision is required to start a session", () => {
  it("starts when a named operator acknowledges the policy on an open req", () => {
    const s = startSession(OK);
    expect(s.status).toBe("in-progress");
    expect(s.operator).toBe("Dana Whitfield");
    expect(s.acknowledgedPolicy).toBe(true);
  });

  it("refuses without a named operator", () => {
    expect(() => startSession({ ...OK, operator: "   " })).toThrow(SupervisionRequiredError);
  });

  it("refuses without an explicit acknowledgement", () => {
    expect(() => startSession({ ...OK, acknowledgedPolicy: false })).toThrow(
      /acknowledge the supervision policy/,
    );
  });

  it("refuses when the requisition is not open for sourcing", () => {
    expect(() => startSession({ ...OK, reqAcceptsSourcing: false })).toThrow(
      /not open for sourcing/,
    );
  });

  it("states a policy the operator must accept", () => {
    expect(ManualProvider.supervisionPolicy.length).toBeGreaterThan(0);
    expect(ManualProvider.supervisionPolicy.join(" ")).toMatch(/personally/i);
  });
});

describe("manual capture only", () => {
  it("records a candidate the operator captured under supervision", () => {
    const s = startSession(OK);
    const c = newCandidate({ fullName: "Priya Raman", origin: "supervised-session" });
    expect(recordManualCapture(s, c).candidatesAdded).toBe(1);
  });

  it("refuses a candidate not marked as supervised capture", () => {
    const s = startSession(OK);
    const c = newCandidate({ fullName: "Priya Raman", origin: "inbound" });
    expect(() => recordManualCapture(s, c)).toThrow(/origin must be "supervised-session"/);
  });

  it("refuses capture after the session ended", () => {
    const ended = completeSession(startSession(OK));
    const c = newCandidate({ fullName: "X", origin: "supervised-session" });
    expect(() => recordManualCapture(ended, c)).toThrow(/not in progress/);
  });

  it("refuses to end a session twice", () => {
    const ended = completeSession(startSession(OK));
    expect(() => completeSession(ended)).toThrow(SupervisionRequiredError);
  });

  it("endSession is a deprecated alias that forwards to completeSession", () => {
    // Kept so Increment 1 callers keep working. It must stay a forwarder, not
    // a second implementation — that duplication is what this replaced.
    const viaAlias = endSession(startSession(OK), "note");
    expect(viaAlias.status).toBe("ended");
    expect(viaAlias.notes).toBe("note");
    expect(() => endSession(viaAlias)).toThrow(SupervisionRequiredError);
  });

  it("stamps an end time", () => {
    const ended = completeSession(startSession(OK), "Reviewed 12 profiles");
    expect(ended.status).toBe("ended");
    expect(ended.endedAt).toBeTruthy();
    expect(ended.notes).toBe("Reviewed 12 profiles");
  });
});

describe("prohibited capabilities are not implemented", () => {
  it.each(PROHIBITED_CAPABILITIES)("does not support %s", (capability) => {
    expect(supportsCapability(capability)).toBe(false);
  });

  it("names every boundary the product refuses to cross", () => {
    expect(PROHIBITED_CAPABILITIES).toContain("unattended-scraping");
    expect(PROHIBITED_CAPABILITIES).toContain("rate-limit-bypass");
    expect(PROHIBITED_CAPABILITIES).toContain("automation-evasion");
  });
});

describe("session history", () => {
  it("returns a requisition's sessions, newest first", () => {
    const a = startSession(OK);
    const b = { ...startSession(OK), startedAt: "2099-01-01T00:00:00.000Z" };
    const other = startSession({ ...OK, reqId: "req_2" });
    const forReq = sessionsForReq("req_1", [a, b, other]);
    expect(forReq).toHaveLength(2);
    expect(forReq[0].startedAt).toBe("2099-01-01T00:00:00.000Z");
  });
});
