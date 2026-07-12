import { describe, it, expect } from "vitest";
import type { CueEvent } from "./types";
import { assist, ASSIST_ACTIONS, type AssistContext, type AssistAction } from "./comms-ai";

const event: CueEvent = {
  id: "evt-1",
  title: "Q3 Founder Summit",
  classification: "founder",
  summary: "",
  startsAt: "2026-08-01T18:00:00.000Z",
  timezone: "America/Los_Angeles",
  status: "upcoming",
  venue: "Pier 27 Pavilion",
  city: "San Francisco",
  host: "Dana Whitfield",
  capacity: { maxAttendees: 500, rsvpEnabled: true, waitlistEnabled: true },
  confirmedGuests: 320,
  invitedGuests: 481,
  branding: { theme: "indigo" },
  portfolio: [],
  tags: [],
  createdAt: "2026-07-01T00:00:00.000Z",
};

function ctx(over: Partial<AssistContext> = {}): AssistContext {
  return {
    event,
    stage: "invitations",
    subject: "",
    message: "",
    audienceLabel: "Everyone",
    ...over,
  };
}

describe("generators produce subject + message from the event", () => {
  const gens: AssistAction[] = [
    "generate-invitation",
    "generate-follow-up",
    "generate-thank-you",
    "generate-survey",
  ];

  for (const action of gens) {
    it(`${action} fills subject and message and mentions the event`, () => {
      const r = assist(action, ctx());
      expect(r.subject && r.subject.length).toBeTruthy();
      expect(r.message).toContain("Q3 Founder Summit");
      expect(r.message).toContain("{{first_name}}");
      expect(r.note.length).toBeGreaterThan(0);
    });
  }
});

describe("transforms reshape the current draft", () => {
  const draft = "Hey! wanna come hang out? thanks";

  it("rewrite-professional removes casual language and exclamation", () => {
    const r = assist("rewrite-professional", ctx({ message: draft }));
    expect(r.message).toBeTruthy();
    expect(r.message).not.toContain("!");
    expect(r.message?.toLowerCase()).not.toContain("wanna");
    expect(r.message).toContain("Dana Whitfield");
  });

  it("rewrite-friendly opens warmly and signs with a first name", () => {
    const r = assist("rewrite-friendly", ctx({ message: "Dear guest, please attend." }));
    expect(r.message).toContain("Hey {{first_name}}");
    expect(r.message).toContain("Dana");
  });

  it("shorten keeps only the first sentences", () => {
    const long = "One. Two. Three. Four. Five.";
    const r = assist("shorten", ctx({ message: long }));
    expect(r.message).toContain("One. Two.");
    expect(r.message).not.toContain("Four.");
  });

  it("expand grows the draft", () => {
    const short = "You're invited.";
    const r = assist("expand", ctx({ message: short }));
    expect(r.message!.length).toBeGreaterThan(short.length);
    expect(r.message).toContain("You're invited.");
  });

  it("transforms fall back to a seed draft when the message is empty", () => {
    const r = assist("rewrite-professional", ctx({ message: "" }));
    expect(r.message).toContain("Q3 Founder Summit");
  });
});

describe("subject-lines", () => {
  it("returns five stage-specific options and no message", () => {
    const r = assist("subject-lines", ctx({ stage: "rsvp-reminders" }));
    expect(r.subjects).toHaveLength(5);
    expect(r.message).toBeUndefined();
    expect(r.subjects!.every((s) => s.length > 0)).toBe(true);
  });

  it("subject lines differ by stage", () => {
    const inv = assist("subject-lines", ctx({ stage: "invitations" })).subjects!;
    const survey = assist("subject-lines", ctx({ stage: "survey" })).subjects!;
    expect(inv).not.toEqual(survey);
  });
});

describe("ASSIST_ACTIONS catalog", () => {
  it("every catalog action is handled by assist()", () => {
    for (const meta of ASSIST_ACTIONS) {
      const r = assist(meta.action, ctx());
      const hasOutput = Boolean(r.subject || r.message || r.subjects);
      expect(hasOutput).toBe(true);
    }
  });

  it("lists the nine specified actions", () => {
    expect(ASSIST_ACTIONS).toHaveLength(9);
  });
});
