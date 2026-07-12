import { describe, it, expect } from "vitest";
import type { Guest, GuestRole, RsvpStatus } from "./types";
import { newGuest } from "./guests-select";
import {
  matchesAudience,
  resolveAudience,
  audienceCount,
  audienceLabel,
  projectMetrics,
  deliveryRate,
  openRate,
  clickRate,
  responseRate,
  campaignsForEvent,
  campaignsForStage,
  stageCounts,
  workspaceMetrics,
  newCampaign,
  emptyMetrics,
  compact,
  pct,
  STAGE_ORDER,
  STAGE_META,
  type Campaign,
  type CampaignStage,
} from "./comms";

const NOW_ISO = "2026-07-08T18:00:00.000Z";

interface GOpts {
  id: string;
  rsvp?: RsvpStatus;
  vip?: boolean;
  roles?: GuestRole[];
  tags?: string[];
  portfolio?: string;
}

function guest(o: GOpts): Guest {
  const g = newGuest("evt-1", o.id, NOW_ISO);
  return {
    ...g,
    roles: o.roles ?? [],
    vip: o.vip ?? false,
    tags: o.tags ?? [],
    professional: { ...g.professional, portfolioCompanyId: o.portfolio },
    attendance: { ...g.attendance, rsvp: o.rsvp ?? "invited" },
  };
}

const roster: Guest[] = [
  guest({ id: "g1", rsvp: "confirmed", vip: true, roles: ["founder"], tags: ["keynote"] }),
  guest({ id: "g2", rsvp: "invited" }),
  guest({ id: "g3", rsvp: "tentative", roles: ["sponsor"] }),
  guest({ id: "g4", rsvp: "declined" }),
  guest({ id: "g5", rsvp: "confirmed", portfolio: "co-lattice", tags: ["vip-guest"] }),
  guest({ id: "g6", rsvp: "confirmed", vip: true, tags: ["Speaker"] }),
];

describe("matchesAudience", () => {
  it("everyone matches all guests", () => {
    expect(roster.every((g) => matchesAudience(g, "everyone"))).toBe(true);
  });

  it("confirmed / pending / declined partition by RSVP", () => {
    expect(roster.filter((g) => matchesAudience(g, "confirmed")).map((g) => g.id)).toEqual([
      "g1",
      "g5",
      "g6",
    ]);
    // pending = invited OR tentative
    expect(roster.filter((g) => matchesAudience(g, "pending")).map((g) => g.id)).toEqual([
      "g2",
      "g3",
    ]);
    expect(roster.filter((g) => matchesAudience(g, "declined")).map((g) => g.id)).toEqual(["g4"]);
  });

  it("vips matches the vip flag", () => {
    expect(roster.filter((g) => matchesAudience(g, "vips")).map((g) => g.id)).toEqual(["g1", "g6"]);
  });

  it("speakers matches speaker/keynote tags case-insensitively", () => {
    expect(roster.filter((g) => matchesAudience(g, "speakers")).map((g) => g.id)).toEqual([
      "g1",
      "g6",
    ]);
  });

  it("sponsors matches the sponsor role", () => {
    expect(roster.filter((g) => matchesAudience(g, "sponsors")).map((g) => g.id)).toEqual(["g3"]);
  });

  it("portfolio matches a portfolio tie", () => {
    expect(roster.filter((g) => matchesAudience(g, "portfolio")).map((g) => g.id)).toEqual(["g5"]);
  });

  it("custom matches an exact tag, and nothing without a tag", () => {
    expect(matchesAudience(roster[4], "custom", "vip-guest")).toBe(true);
    expect(matchesAudience(roster[4], "custom", "missing")).toBe(false);
    expect(matchesAudience(roster[4], "custom")).toBe(false);
  });
});

describe("resolveAudience / audienceCount", () => {
  it("resolve and count agree", () => {
    const list = resolveAudience(roster, "confirmed");
    expect(list).toHaveLength(3);
    expect(audienceCount(roster, "confirmed")).toBe(3);
  });
});

describe("audienceLabel", () => {
  it("folds the custom tag into a hashtag", () => {
    expect(audienceLabel("custom", "founders")).toBe("#founders");
    expect(audienceLabel("custom")).toBe("Custom tag");
    expect(audienceLabel("vips")).toBe("VIPs");
  });
});

describe("projectMetrics", () => {
  it("returns empty metrics for a zero-recipient send", () => {
    expect(projectMetrics(0, "invitations", "x")).toEqual(emptyMetrics());
  });

  it("is deterministic for the same inputs", () => {
    const a = projectMetrics(300, "invitations", "cmp-1");
    const b = projectMetrics(300, "invitations", "cmp-1");
    expect(a).toEqual(b);
  });

  it("keeps the funnel monotonic: recipients ≥ delivered ≥ opened ≥ clicked, responded", () => {
    for (const stage of STAGE_ORDER) {
      const m = projectMetrics(420, stage, `seed-${stage}`);
      expect(m.recipients).toBeGreaterThanOrEqual(m.delivered);
      expect(m.delivered).toBeGreaterThanOrEqual(m.opened);
      expect(m.opened).toBeGreaterThanOrEqual(m.clicked);
      expect(m.opened).toBeGreaterThanOrEqual(m.responded);
    }
  });

  it("varies engagement by stage (a VIP note out-opens a save-the-date)", () => {
    const vip = projectMetrics(200, "vip-outreach", "s");
    const std = projectMetrics(200, "save-the-date", "s");
    expect(vip.opened).toBeGreaterThan(std.opened);
  });
});

describe("rate helpers guard divide-by-zero", () => {
  it("all rates are 0 on empty metrics", () => {
    const m = emptyMetrics();
    expect(deliveryRate(m)).toBe(0);
    expect(openRate(m)).toBe(0);
    expect(clickRate(m)).toBe(0);
    expect(responseRate(m)).toBe(0);
  });

  it("computes rates over the right base", () => {
    const m = { recipients: 100, delivered: 100, opened: 50, clicked: 10, responded: 25 };
    expect(deliveryRate(m)).toBe(1);
    expect(openRate(m)).toBe(0.5);
    expect(clickRate(m)).toBe(0.2);
    expect(responseRate(m)).toBe(0.25);
  });
});

// --- Selection + rollups ----------------------------------------------------

function campaign(over: Partial<Campaign> & { id: string; stage: CampaignStage }): Campaign {
  return {
    eventId: "evt-1",
    title: "C",
    audience: "everyone",
    subject: "",
    message: "",
    status: "draft",
    metrics: emptyMetrics(),
    createdAt: NOW_ISO,
    updatedAt: NOW_ISO,
    ...over,
  };
}

describe("campaign selection", () => {
  const list: Campaign[] = [
    campaign({ id: "c1", stage: "invitations", updatedAt: "2026-07-01T00:00:00Z" }),
    campaign({ id: "c2", stage: "invitations", updatedAt: "2026-07-05T00:00:00Z" }),
    campaign({ id: "c3", stage: "thank-you", updatedAt: "2026-07-03T00:00:00Z" }),
    campaign({ id: "c4", stage: "invitations", eventId: "evt-2", updatedAt: "2026-07-09T00:00:00Z" }),
  ];

  it("campaignsForEvent filters by event and sorts newest-updated first", () => {
    expect(campaignsForEvent(list, "evt-1").map((c) => c.id)).toEqual(["c2", "c3", "c1"]);
  });

  it("campaignsForStage narrows to a stage of an event", () => {
    expect(campaignsForStage(list, "evt-1", "invitations").map((c) => c.id)).toEqual(["c2", "c1"]);
  });

  it("stageCounts counts per stage for one event, zero elsewhere", () => {
    const counts = stageCounts(list, "evt-1");
    expect(counts.invitations).toBe(2);
    expect(counts["thank-you"]).toBe(1);
    expect(counts.survey).toBe(0);
    // every stage key present
    expect(Object.keys(counts).sort()).toEqual([...STAGE_ORDER].sort());
  });
});

describe("workspaceMetrics", () => {
  it("rolls up sent campaigns and counts scheduled ones", () => {
    const list: Campaign[] = [
      campaign({
        id: "s1",
        stage: "invitations",
        status: "sent",
        metrics: { recipients: 100, delivered: 98, opened: 60, clicked: 20, responded: 30 },
      }),
      campaign({
        id: "s2",
        stage: "rsvp-reminders",
        status: "sent",
        metrics: { recipients: 50, delivered: 50, opened: 20, clicked: 5, responded: 10 },
      }),
      campaign({ id: "s3", stage: "event-updates", status: "scheduled" }),
      campaign({ id: "s4", stage: "day-before", status: "draft" }),
    ];
    const m = workspaceMetrics(list, "evt-1");
    expect(m.sent).toBe(148); // delivered total
    expect(m.delivered).toBe(148);
    expect(m.opened).toBe(80);
    expect(m.clicked).toBe(25);
    expect(m.responded).toBe(40);
    expect(m.scheduled).toBe(1);
    expect(m.deliveryRate).toBeCloseTo(148 / 150);
    expect(m.rsvpRate).toBeCloseTo(40 / 148);
  });

  it("is all-zero when nothing has been sent", () => {
    const m = workspaceMetrics([campaign({ id: "d", stage: "invitations" })], "evt-1");
    expect(m).toMatchObject({ sent: 0, opened: 0, scheduled: 0, deliveryRate: 0, rsvpRate: 0 });
  });
});

describe("formatting", () => {
  it("pct rounds to whole percent", () => {
    expect(pct(0.5)).toBe("50%");
    expect(pct(0.987)).toBe("99%");
  });

  it("compact abbreviates thousands", () => {
    expect(compact(940)).toBe("940");
    expect(compact(1000)).toBe("1k");
    expect(compact(1240)).toBe("1.2k");
  });
});

describe("newCampaign", () => {
  it("uses the stage's default audience and a draft status", () => {
    const c = newCampaign("c-new", "evt-1", "rsvp-reminders", NOW_ISO);
    expect(c.status).toBe("draft");
    expect(c.audience).toBe(STAGE_META["rsvp-reminders"].defaultAudience);
    expect(c.audience).toBe("pending");
    expect(c.metrics).toEqual(emptyMetrics());
  });
});
