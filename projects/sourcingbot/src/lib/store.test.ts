/**
 * @vitest-environment jsdom
 *
 * Store seam: normalization, the duplicate-on-req guard, and the invariant
 * that persistence keeps Candidate and ReqCandidate as separate collections.
 */
import { describe, it, expect, beforeEach } from "vitest";
import {
  DuplicateReqCandidateError,
  __resetStore,
  __seedStore,
  addCandidate,
  addReq,
  addReqCandidate,
  briefForReq,
  getCandidate,
  getReq,
  getState,
  updateReqCandidate,
} from "./store";
import { newReq, transition } from "./req";
import { newCandidate } from "./candidate";
import { advance, newReqCandidate, reqHistoryFor } from "./req-candidate";
import { __resetIdCounter } from "./ids";

beforeEach(() => {
  localStorage.clear();
  __resetIdCounter();
  __resetStore();
});

describe("workspace persistence", () => {
  it("starts empty after a reset", () => {
    const s = getState();
    expect(s.reqs).toEqual([]);
    expect(s.candidates).toEqual([]);
    expect(s.reqCandidates).toEqual([]);
  });

  it("keeps the five collections separate and normalized", () => {
    expect(Object.keys(getState()).sort()).toEqual([
      "briefs",
      "candidates",
      "reqCandidates",
      "reqs",
      "sessions",
    ]);
  });

  it("stores no person data inside a pipeline row", () => {
    const person = newCandidate({ fullName: "Priya Raman", origin: "referral" });
    addCandidate(person);
    addReqCandidate(newReqCandidate({ reqId: "req_1", candidateId: person.id, briefVersion: 1, by: "x" }));

    const serialized = JSON.stringify(getState().reqCandidates);
    expect(serialized).not.toContain("Priya Raman");
    expect(serialized).toContain(person.id);
  });

  it("writes through to localStorage", () => {
    addReq(newReq({ code: "REQ-1", title: "Eng", team: "Infra", location: "Boston" }));
    const raw = localStorage.getItem("sourcingbot.workspace.v1");
    expect(raw).toBeTruthy();
    expect(JSON.parse(raw as string).reqs).toHaveLength(1);
  });

  it("looks up reqs and candidates by id", () => {
    const r = newReq({ code: "REQ-1", title: "Eng", team: "Infra", location: "Boston" });
    const c = newCandidate({ fullName: "X", origin: "inbound" });
    addReq(r);
    addCandidate(c);
    expect(getReq(r.id)?.code).toBe("REQ-1");
    expect(getCandidate(c.id)?.fullName).toBe("X");
    expect(getReq("nope")).toBeUndefined();
  });
});

describe("one person, many requisitions", () => {
  it("attaches the same candidate to two reqs", () => {
    const person = newCandidate({ fullName: "Priya Raman", origin: "referral" });
    addCandidate(person);
    addReqCandidate(newReqCandidate({ reqId: "req_a", candidateId: person.id, briefVersion: 1, by: "x" }));
    addReqCandidate(newReqCandidate({ reqId: "req_b", candidateId: person.id, briefVersion: 1, by: "x" }));

    expect(getState().candidates).toHaveLength(1);
    expect(reqHistoryFor(person.id, getState().reqCandidates)).toHaveLength(2);
  });

  it("refuses the same person twice on one req", () => {
    const person = newCandidate({ fullName: "Priya Raman", origin: "referral" });
    addCandidate(person);
    const row = newReqCandidate({ reqId: "req_a", candidateId: person.id, briefVersion: 1, by: "x" });
    addReqCandidate(row);
    expect(() =>
      addReqCandidate(newReqCandidate({ reqId: "req_a", candidateId: person.id, briefVersion: 1, by: "y" })),
    ).toThrow(DuplicateReqCandidateError);
    expect(getState().reqCandidates).toHaveLength(1);
  });

  it("advances one req without touching the other", () => {
    const person = newCandidate({ fullName: "Priya Raman", origin: "referral" });
    addCandidate(person);
    const a = newReqCandidate({ reqId: "req_a", candidateId: person.id, briefVersion: 1, by: "x" });
    const b = newReqCandidate({ reqId: "req_b", candidateId: person.id, briefVersion: 1, by: "x" });
    addReqCandidate(a);
    addReqCandidate(b);

    updateReqCandidate(advance(a, "reviewing", "Dana"));

    const rows = getState().reqCandidates;
    expect(rows.find((r) => r.id === a.id)?.stage).toBe("reviewing");
    expect(rows.find((r) => r.id === b.id)?.stage).toBe("identified");
  });
});

describe("seeded demo workspace", () => {
  beforeEach(() => __seedStore());

  it("seeds reqs, briefs, people and pipeline rows", () => {
    const s = getState();
    expect(s.reqs.length).toBeGreaterThan(0);
    expect(s.candidates.length).toBeGreaterThan(0);
    expect(s.briefs.length).toBeGreaterThan(0);
  });

  it("demonstrates one person on two requisitions", () => {
    const history = reqHistoryFor("c_priya", getState().reqCandidates);
    expect(history).toHaveLength(2);
    expect(new Set(history.map((h) => h.reqId)).size).toBe(2);
    expect(new Set(history.map((h) => h.stage)).size).toBe(2);
  });

  it("seeds no LinkedIn URLs, so no capture is implied", () => {
    expect(getState().candidates.every((c) => !c.linkedInUrl)).toBe(true);
  });

  it("attaches a brief to the open infra req", () => {
    expect(briefForReq("req_infra")?.version).toBe(4);
  });

  it("has an open req that accepts sourcing", () => {
    const open = getState().reqs.find((r) => r.status === "open");
    expect(open).toBeDefined();
  });

  it("keeps closed reqs out of sourcing", () => {
    const closed = getState().reqs.find((r) => r.status === "closed");
    expect(closed).toBeDefined();
    expect(() => transition(closed!, "open")).toThrow();
  });
});
