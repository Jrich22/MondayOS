import { describe, it, expect, beforeEach } from "vitest";
import {
  currentCompany,
  currentRole,
  findPossibleDuplicates,
  identityKey,
  newCandidate,
  searchCandidates,
  talentConcentration,
  updateCandidate,
} from "./candidate";
import { __resetIdCounter } from "./ids";

beforeEach(() => __resetIdCounter());

describe("Candidate — the persistent person", () => {
  it("carries no requisition scope", () => {
    const c = newCandidate({ fullName: "Priya Raman", origin: "referral" });
    for (const field of ["reqId", "stage", "fitScore", "briefVersion", "assessments"]) {
      expect(c).not.toHaveProperty(field);
    }
  });

  it("trims and normalizes on construction", () => {
    const c = newCandidate({
      fullName: "  Priya Raman  ",
      skills: ["Go", "go", " Kubernetes ", ""],
      origin: "manual-entry",
    });
    expect(c.fullName).toBe("Priya Raman");
    expect(c.skills).toEqual(["Go", "Kubernetes"]);
  });

  it("omits empty optional contact fields rather than storing blanks", () => {
    const c = newCandidate({ fullName: "X", email: "  ", linkedInUrl: "", origin: "inbound" });
    expect(c.email).toBeUndefined();
    expect(c.linkedInUrl).toBeUndefined();
  });

  it("preserves id and createdAt through updates", () => {
    const c = newCandidate({ fullName: "X", origin: "inbound" });
    const updated = updateCandidate(c, { headline: "Staff Engineer" });
    expect(updated.id).toBe(c.id);
    expect(updated.createdAt).toBe(c.createdAt);
    expect(updated.headline).toBe("Staff Engineer");
  });

  it("mints unique ids even within the same millisecond", () => {
    const ids = Array.from({ length: 200 }, () =>
      newCandidate({ fullName: "X", origin: "inbound" }).id,
    );
    expect(new Set(ids).size).toBe(200);
  });
});

describe("career history", () => {
  const withRoles = () =>
    newCandidate({
      fullName: "Priya Raman",
      origin: "referral",
      roles: [
        { title: "Senior SRE", company: "Arcadia", startedAt: "2018-06", endedAt: "2022-02" },
        { title: "Staff Infra", company: "Northwind", startedAt: "2022-03" },
      ],
    });

  it("treats the open role as current", () => {
    expect(currentRole(withRoles())?.company).toBe("Northwind");
    expect(currentCompany(withRoles())).toBe("Northwind");
  });

  it("falls back to the most recent ended role", () => {
    const c = newCandidate({
      fullName: "X",
      origin: "inbound",
      roles: [
        { title: "A", company: "Old", startedAt: "2015-01", endedAt: "2018-01" },
        { title: "B", company: "Newer", startedAt: "2018-02", endedAt: "2021-01" },
      ],
    });
    expect(currentCompany(c)).toBe("Newer");
  });

  it("returns null with no roles", () => {
    expect(currentRole(newCandidate({ fullName: "X", origin: "inbound" }))).toBeNull();
    expect(currentCompany(newCandidate({ fullName: "X", origin: "inbound" }))).toBe("");
  });
});

describe("duplicate detection", () => {
  it("keys on email when present", () => {
    const a = newCandidate({ fullName: "Priya Raman", email: "P@Example.com", origin: "inbound" });
    const b = newCandidate({ fullName: "P. Raman", email: "p@example.com", origin: "referral" });
    expect(identityKey(a)).toBe(identityKey(b));
    expect(findPossibleDuplicates(a, [b])).toHaveLength(1);
  });

  it("falls back to name plus current company", () => {
    const roles = [{ title: "Eng", company: "Northwind", startedAt: "2022-01" }];
    const a = newCandidate({ fullName: "Priya Raman", roles, origin: "inbound" });
    const b = newCandidate({ fullName: "priya  raman", roles, origin: "manual-entry" });
    expect(findPossibleDuplicates(a, [b])).toHaveLength(1);
  });

  it("does not flag the same name at different companies", () => {
    const a = newCandidate({
      fullName: "Priya Raman",
      roles: [{ title: "Eng", company: "Northwind", startedAt: "2022-01" }],
      origin: "inbound",
    });
    const b = newCandidate({
      fullName: "Priya Raman",
      roles: [{ title: "Eng", company: "Helix", startedAt: "2022-01" }],
      origin: "inbound",
    });
    expect(findPossibleDuplicates(a, [b])).toHaveLength(0);
  });

  it("never reports a record against itself", () => {
    const a = newCandidate({ fullName: "X", email: "x@y.com", origin: "inbound" });
    expect(findPossibleDuplicates(a, [a])).toHaveLength(0);
  });
});

describe("talent concentration", () => {
  it("counts people per current employer, busiest first", () => {
    const at = (company: string, fullName: string) =>
      newCandidate({
        fullName,
        origin: "inbound",
        roles: [{ title: "Eng", company, startedAt: "2022-01" }],
      });
    const result = talentConcentration([
      at("Northwind", "A"),
      at("Northwind", "B"),
      at("Helix", "C"),
    ]);
    expect(result[0]).toEqual({ company: "Northwind", count: 2 });
    expect(result[1]).toEqual({ company: "Helix", count: 1 });
  });

  it("skips people with no current company", () => {
    expect(talentConcentration([newCandidate({ fullName: "X", origin: "inbound" })])).toEqual([]);
  });
});

describe("search", () => {
  const pool = [
    newCandidate({
      fullName: "Priya Raman",
      skills: ["Kubernetes", "Go"],
      roles: [{ title: "Staff", company: "Northwind", startedAt: "2022-01" }],
      origin: "referral",
    }),
    newCandidate({ fullName: "Lena Ostrowski", skills: ["PyTorch"], origin: "inbound" }),
  ];

  it("returns everything for an empty query", () => {
    expect(searchCandidates(pool, "   ")).toHaveLength(2);
  });

  it("matches on skill and company", () => {
    expect(searchCandidates(pool, "kubernetes")[0].fullName).toBe("Priya Raman");
    expect(searchCandidates(pool, "northwind")).toHaveLength(1);
  });

  it("requires every term to match", () => {
    expect(searchCandidates(pool, "priya pytorch")).toHaveLength(0);
  });
});
