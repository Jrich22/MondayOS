import { describe, it, expect } from "vitest";
import type { Guest } from "./types";
import {
  newGuest,
  displayName,
  fullName,
  initials,
  guestCompany,
  matchesGuestQuery,
  selectGuests,
  sortGuests,
  guestSummary,
  uniqueCompanies,
  uniqueTags,
  hasActiveFilters,
  emptyFilters,
  withCheckIn,
} from "./guests-select";

const NOW = "2026-07-08T18:00:00.000Z";

function make(over: Partial<Guest> & { id: string }): Guest {
  const base = newGuest("evt-1", over.id, NOW);
  return {
    ...base,
    ...over,
    identity: { ...base.identity, ...over.identity },
    professional: { ...base.professional, ...over.professional },
    attendance: { ...base.attendance, ...over.attendance },
  };
}

describe("identity helpers", () => {
  const g = make({
    id: "g1",
    identity: { firstName: "Ava", lastName: "Chen", preferredName: "A" },
  });

  it("fullName uses legal name; displayName prefers the preferred name", () => {
    expect(fullName(g)).toBe("Ava Chen");
    expect(displayName(g)).toBe("A Chen");
  });

  it("displayName falls back to first name without a preferred name", () => {
    expect(displayName(make({ id: "g2", identity: { firstName: "Ben", lastName: "Sable" } }))).toBe(
      "Ben Sable",
    );
  });

  it("initials take the first letter of first and last name", () => {
    expect(initials(g)).toBe("AC");
  });
});

describe("guestCompany", () => {
  const portfolio = [{ id: "co-1", name: "Lattice AI", stage: "Seed" as const }];

  it("resolves a portfolio tie by id over the free-text company", () => {
    const g = make({
      id: "g1",
      professional: { company: "Old Co", portfolioCompanyId: "co-1" },
    });
    expect(guestCompany(g, portfolio)).toBe("Lattice AI");
  });

  it("falls back to the free-text company when there is no tie", () => {
    const g = make({ id: "g2", professional: { company: "Northwind" } });
    expect(guestCompany(g, portfolio)).toBe("Northwind");
  });
});

describe("matchesGuestQuery", () => {
  const g = make({
    id: "g1",
    identity: { firstName: "Ava", lastName: "Chen", email: "ava@lattice.ai" },
    professional: { company: "Lattice AI", jobTitle: "CEO" },
    tags: ["speaker"],
  });

  it("matches across name, email, company, title, and tags", () => {
    expect(matchesGuestQuery(g, "chen")).toBe(true);
    expect(matchesGuestQuery(g, "lattice")).toBe(true);
    expect(matchesGuestQuery(g, "ceo")).toBe(true);
    expect(matchesGuestQuery(g, "speaker")).toBe(true);
    expect(matchesGuestQuery(g, "ava@")).toBe(true);
  });

  it("is empty-query permissive and misses non-substrings", () => {
    expect(matchesGuestQuery(g, "")).toBe(true);
    expect(matchesGuestQuery(g, "zzz")).toBe(false);
  });
});

describe("selectGuests filtering", () => {
  const roster: Guest[] = [
    make({
      id: "vip",
      identity: { firstName: "Ava", lastName: "Chen" },
      professional: { company: "Lattice AI", portfolioCompanyId: "co-1" },
      roles: ["founder"],
      vip: true,
      attendance: { rsvp: "confirmed", checkedIn: true, noShow: false, waitlisted: false, plusOnes: 0 },
      tags: ["speaker"],
    }),
    make({
      id: "press",
      identity: { firstName: "Elena", lastName: "Petrova" },
      professional: { company: "The Information" },
      roles: ["press"],
      attendance: { rsvp: "tentative", checkedIn: false, noShow: false, waitlisted: false, plusOnes: 0 },
      tags: ["press"],
    }),
  ];

  const base = emptyFilters();

  it("filters by VIP", () => {
    expect(selectGuests(roster, { ...base, vipOnly: true }, "name").map((g) => g.id)).toEqual(["vip"]);
  });

  it("filters by checked-in", () => {
    expect(selectGuests(roster, { ...base, checkedInOnly: true }, "name").map((g) => g.id)).toEqual([
      "vip",
    ]);
  });

  it("filters by role, rsvp, portfolio, company, and tag", () => {
    expect(selectGuests(roster, { ...base, role: "press" }, "name").map((g) => g.id)).toEqual(["press"]);
    expect(selectGuests(roster, { ...base, rsvp: "confirmed" }, "name").map((g) => g.id)).toEqual(["vip"]);
    expect(
      selectGuests(roster, { ...base, portfolioCompanyId: "co-1" }, "name").map((g) => g.id),
    ).toEqual(["vip"]);
    expect(selectGuests(roster, { ...base, company: "The Information" }, "name").map((g) => g.id)).toEqual(
      ["press"],
    );
    expect(selectGuests(roster, { ...base, tag: "press" }, "name").map((g) => g.id)).toEqual(["press"]);
  });
});

describe("sortGuests", () => {
  const a = make({ id: "a", identity: { firstName: "Ava", lastName: "Adams" }, vip: false });
  const b = make({
    id: "b",
    identity: { firstName: "Ben", lastName: "Baker" },
    vip: true,
    professional: { company: "Zeta" },
    attendance: { rsvp: "confirmed", checkedIn: true, checkedInAt: NOW, noShow: false, waitlisted: false, plusOnes: 0 },
  });

  it("alphabetical sorts by last name", () => {
    expect(sortGuests([b, a], "name").map((g) => g.id)).toEqual(["a", "b"]);
  });

  it("vip sort puts VIPs first", () => {
    expect(sortGuests([a, b], "vip").map((g) => g.id)).toEqual(["b", "a"]);
  });

  it("checkin sort puts the checked-in ahead of the not-checked-in", () => {
    expect(sortGuests([a, b], "checkin").map((g) => g.id)).toEqual(["b", "a"]);
  });
});

describe("guestSummary", () => {
  it("counts totals, confirmed, checked-in, VIPs and expected headcount with plus-ones", () => {
    const roster = [
      make({
        id: "a",
        vip: true,
        attendance: { rsvp: "confirmed", checkedIn: true, noShow: false, waitlisted: false, plusOnes: 1 },
      }),
      make({
        id: "b",
        attendance: { rsvp: "confirmed", checkedIn: false, noShow: false, waitlisted: false, plusOnes: 0 },
      }),
      make({
        id: "c",
        attendance: { rsvp: "declined", checkedIn: false, noShow: false, waitlisted: false, plusOnes: 0 },
      }),
    ];
    const s = guestSummary(roster);
    expect(s).toEqual({ total: 3, confirmed: 2, checkedIn: 1, vip: 1, expectedHeadcount: 3 });
  });
});

describe("facets and filter state", () => {
  const roster = [
    make({ id: "a", professional: { company: "Beta" }, tags: ["x", "y"] }),
    make({ id: "b", professional: { company: "Alpha" }, tags: ["y"] }),
  ];

  it("uniqueCompanies and uniqueTags are sorted and de-duped", () => {
    expect(uniqueCompanies(roster)).toEqual(["Alpha", "Beta"]);
    expect(uniqueTags(roster)).toEqual(["x", "y"]);
  });

  it("hasActiveFilters reflects any active constraint", () => {
    expect(hasActiveFilters(emptyFilters())).toBe(false);
    expect(hasActiveFilters({ ...emptyFilters(), vipOnly: true })).toBe(true);
    expect(hasActiveFilters({ ...emptyFilters(), query: "ava" })).toBe(true);
  });
});

describe("withCheckIn", () => {
  it("stamps checkedInAt on check-in and clears a stale no-show", () => {
    const g = make({
      id: "a",
      attendance: { rsvp: "confirmed", checkedIn: false, noShow: true, waitlisted: false, plusOnes: 0 },
    });
    const after = withCheckIn(g, true, NOW);
    expect(after.attendance.checkedIn).toBe(true);
    expect(after.attendance.checkedInAt).toBe(NOW);
    expect(after.attendance.noShow).toBe(false);
    expect(g.attendance.checkedIn).toBe(false); // input untouched
  });

  it("clears checkedInAt on check-out", () => {
    const g = withCheckIn(make({ id: "a" }), true, NOW);
    const out = withCheckIn(g, false, "2026-07-08T19:00:00.000Z");
    expect(out.attendance.checkedIn).toBe(false);
    expect(out.attendance.checkedInAt).toBeUndefined();
  });
});
