import { describe, it, expect } from "vitest";
import { draftDescription, suggestAgenda, suggestInviteCopy, assistText } from "./ai";
import type { AssistInput } from "./ai";

const input: AssistInput = {
  title: "Founder Summit",
  classification: "founder",
  venue: "Pier 27",
  city: "San Francisco",
};

describe("AI assist generators", () => {
  it("draftDescription mentions the event name and location", () => {
    const text = draftDescription(input);
    expect(text).toContain("Founder Summit");
    expect(text).toContain("Pier 27");
  });

  it("falls back gracefully when the title is blank", () => {
    const text = draftDescription({ ...input, title: "" });
    expect(text).toContain("this event");
  });

  it("suggestAgenda returns an ordered, non-empty list", () => {
    const agenda = suggestAgenda(input);
    expect(agenda.length).toBeGreaterThan(2);
    expect(agenda[0]).toMatch(/welcome/i);
  });

  it("tailors the agenda to the classification", () => {
    expect(suggestAgenda({ ...input, classification: "demo-day" })).toContain("Cohort presentations");
    expect(suggestAgenda({ ...input, classification: "board-meeting" })).toContain("Key decisions & approvals");
  });

  it("suggestInviteCopy includes a call to action", () => {
    expect(suggestInviteCopy(input).toLowerCase()).toContain("rsvp");
  });

  it("assistText renders the agenda as a numbered list", () => {
    const text = assistText("agenda", input);
    expect(text.startsWith("1. ")).toBe(true);
  });
});
