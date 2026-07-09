import { describe, it, expect } from "vitest";
import {
  emptyDraft,
  validateDraft,
  draftToEvent,
  deriveStatus,
  composeIso,
  type EventDraft,
} from "./create";

function draft(over: Partial<EventDraft> = {}): EventDraft {
  return { ...emptyDraft(), title: "Founder Dinner", date: "2026-09-01", ...over };
}

describe("validateDraft (create mode)", () => {
  it("passes a well-formed draft", () => {
    expect(validateDraft(draft())).toEqual({});
  });

  it("requires a title", () => {
    expect(validateDraft(draft({ title: "  " })).title).toBeTruthy();
  });

  it("requires a date and start time when creating", () => {
    const e = validateDraft(draft({ date: "", startTime: "" }));
    expect(e.date).toBeTruthy();
    expect(e.startTime).toBeTruthy();
  });

  it("rejects an end time at or before the start", () => {
    expect(validateDraft(draft({ startTime: "18:00", endTime: "17:00" })).endTime).toBeTruthy();
    expect(validateDraft(draft({ startTime: "18:00", endTime: "18:00" })).endTime).toBeTruthy();
    expect(validateDraft(draft({ startTime: "18:00", endTime: "19:00" })).endTime).toBeUndefined();
  });

  it("requires a label when classification is custom", () => {
    expect(validateDraft(draft({ classification: "custom", customClassification: "" })).customClassification).toBeTruthy();
    expect(validateDraft(draft({ classification: "custom", customClassification: "VIP" })).customClassification).toBeUndefined();
  });

  it("rejects a non-positive or fractional capacity", () => {
    expect(validateDraft(draft({ maxAttendees: "0" })).maxAttendees).toBeTruthy();
    expect(validateDraft(draft({ maxAttendees: "-5" })).maxAttendees).toBeTruthy();
    expect(validateDraft(draft({ maxAttendees: "12.5" })).maxAttendees).toBeTruthy();
    expect(validateDraft(draft({ maxAttendees: "50" })).maxAttendees).toBeUndefined();
  });
});

describe("validateDraft (draft mode)", () => {
  it("only requires a title", () => {
    expect(validateDraft(draft({ date: "", startTime: "" }), "draft")).toEqual({});
    expect(validateDraft(draft({ title: "", date: "", startTime: "" }), "draft").title).toBeTruthy();
  });
});

describe("deriveStatus", () => {
  const now = Date.parse("2026-07-10T12:00:00Z");
  it("is live inside the window, upcoming before, done after", () => {
    expect(deriveStatus(now - 1000, now + 1000, now)).toBe("live");
    expect(deriveStatus(now + 10_000, now + 20_000, now)).toBe("upcoming");
    expect(deriveStatus(now - 20_000, now - 10_000, now)).toBe("done");
  });
});

describe("draftToEvent", () => {
  const opts = { id: "evt-test", now: Date.parse("2026-07-10T12:00:00Z"), host: "Dana" };

  it("composes a full CueEvent from the draft", () => {
    const ev = draftToEvent(
      draft({ maxAttendees: "50", venue: "Angler", address: "132 Embarcadero" }),
      { ...opts, mode: "create" },
    );
    expect(ev.id).toBe("evt-test");
    expect(ev.title).toBe("Founder Dinner");
    expect(ev.capacity.maxAttendees).toBe(50);
    expect(ev.venue).toBe("Angler");
    expect(ev.confirmedGuests).toBe(0);
    expect(ev.status).toBe("upcoming"); // 2026-09-01 is after the fixed "now"
    expect(ev.createdAt).toBe(new Date(opts.now).toISOString());
  });

  it("forces draft status in draft mode regardless of date", () => {
    const ev = draftToEvent(draft(), { ...opts, mode: "draft" });
    expect(ev.status).toBe("draft");
  });

  it("treats a blank capacity as uncapped", () => {
    const ev = draftToEvent(draft({ maxAttendees: "" }), { ...opts, mode: "create" });
    expect(ev.capacity.maxAttendees).toBeNull();
  });

  it("only keeps a waitlist when RSVP + a cap are both set", () => {
    const withCap = draftToEvent(
      draft({ rsvpEnabled: true, waitlistEnabled: true, maxAttendees: "20" }),
      { ...opts, mode: "create" },
    );
    expect(withCap.capacity.waitlistEnabled).toBe(true);

    const noCap = draftToEvent(
      draft({ rsvpEnabled: true, waitlistEnabled: true, maxAttendees: "" }),
      { ...opts, mode: "create" },
    );
    expect(noCap.capacity.waitlistEnabled).toBe(false);

    const noRsvp = draftToEvent(
      draft({ rsvpEnabled: false, waitlistEnabled: true, maxAttendees: "20" }),
      { ...opts, mode: "create" },
    );
    expect(noRsvp.capacity.waitlistEnabled).toBe(false);
  });

  it("stores the custom classification label only when custom", () => {
    const custom = draftToEvent(
      draft({ classification: "custom", customClassification: "VIP" }),
      { ...opts, mode: "create" },
    );
    expect(custom.customClassification).toBe("VIP");

    const standard = draftToEvent(draft({ classification: "investor" }), { ...opts, mode: "create" });
    expect(standard.customClassification).toBeUndefined();
  });
});

describe("composeIso", () => {
  it("produces a valid ISO string from date + time", () => {
    expect(() => new Date(composeIso("2026-09-01", "18:30")).toISOString()).not.toThrow();
    expect(new Date(composeIso("2026-09-01", "18:30")).getFullYear()).toBe(2026);
  });
});
