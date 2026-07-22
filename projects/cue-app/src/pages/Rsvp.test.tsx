/**
 * @vitest-environment jsdom
 *
 * Invite & RSVP slice 1 — guest response surface: route safety, token lifecycle
 * states, and the canonical-state loop (a response updates the Guest record).
 */
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import Rsvp from "./Rsvp";
import type { CueEvent, Guest } from "@/lib/types";
import { createEvent, __resetStore } from "@/lib/store";
import { addGuest, getGuests, __resetGuests } from "@/lib/guests";
import { issueInvitation, revokeInvitation, rotateInvitation, getInvitation, __resetInvitations } from "@/lib/invitation-store";
import { rsvpToken } from "@/lib/invitation";

const EVENT_ID = "evt-test-rsvp";
const GUEST_ID = "g-test-rsvp";

function futureEvent(): CueEvent {
  const starts = new Date(Date.now() + 7 * 86_400_000).toISOString();
  return {
    id: EVENT_ID, title: "Test RSVP Summit", classification: "portfolio", summary: "A test event.",
    startsAt: starts, endsAt: new Date(Date.now() + 7 * 86_400_000 + 3 * 3_600_000).toISOString(),
    timezone: "America/Los_Angeles", status: "upcoming", venue: "HQ", city: "SF", host: "Dana",
    capacity: { maxAttendees: null, rsvpEnabled: true, waitlistEnabled: false },
    confirmedGuests: 0, invitedGuests: 0, branding: { theme: "default" }, portfolio: [], tags: [],
    createdAt: new Date().toISOString(),
  };
}

function testGuest(): Guest {
  return {
    id: GUEST_ID, eventId: EVENT_ID, identity: { firstName: "Riley", lastName: "Guest" },
    professional: {}, roles: [], vip: false,
    attendance: { rsvp: "invited", checkedIn: false, noShow: false, waitlisted: false, plusOnes: 0 },
    preferences: { hotelNeeded: false, transportationNeeded: false },
    communication: { invitationSent: false, reminderSent: false, thankYouSent: false },
    notes: {}, tags: [], seat: null, createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(),
  };
}

beforeEach(() => {
  __resetStore(); __resetGuests(); __resetInvitations();
  createEvent(futureEvent());
  addGuest(testGuest());
});
afterEach(cleanup);

function renderToken(token: string) {
  return render(
    <MemoryRouter initialEntries={[`/rsvp/${encodeURIComponent(token)}`]}>
      <Routes><Route path="/rsvp/:token" element={<Rsvp />} /></Routes>
    </MemoryRouter>,
  );
}

describe("Rsvp route safety & prototype labeling", () => {
  it("always shows the prototype banner", () => {
    renderToken("garbage");
    expect(screen.getByText(/prototype preview/i)).toBeTruthy();
  });
  it("renders an invalid state (no throw) for a malformed token", () => {
    expect(() => renderToken("not-a-real-token")).not.toThrow();
    expect(screen.getByRole('heading').textContent).toMatch(/isn't valid/i);
  });
});

describe("token lifecycle states", () => {
  it("shows revoked after revocation", () => {
    const inv = issueInvitation(EVENT_ID, GUEST_ID);
    const token = rsvpToken(inv);
    revokeInvitation(GUEST_ID);
    renderToken(token);
    expect(screen.getByRole('heading').textContent).toMatch(/revoked/i);
  });
  it("shows replaced/rotated after rotation invalidates the old link", () => {
    const inv = issueInvitation(EVENT_ID, GUEST_ID);
    const oldToken = rsvpToken(inv); // v1
    rotateInvitation(GUEST_ID); // now v2
    renderToken(oldToken);
    expect(screen.getByRole('heading').textContent).toMatch(/replaced/i);
  });
  it("shows an unavailable state when RSVP is disabled for the event (finding #5)", () => {
    __resetStore();
    createEvent({ ...futureEvent(), capacity: { maxAttendees: null, rsvpEnabled: false, waitlistEnabled: false } });
    const inv = issueInvitation(EVENT_ID, GUEST_ID);
    renderToken(rsvpToken(inv));
    expect(screen.getByRole("heading").textContent).toMatch(/RSVP isn't available/i);
  });
});

describe("event timezone on the guest surface (finding #4)", () => {
  it("shows the event's local time with its zone label, not the browser's", () => {
    // 02:00Z is 19:00 the previous day in Los Angeles (the event's timezone).
    __resetStore();
    createEvent({ ...futureEvent(), startsAt: "2026-09-02T02:00:00Z", timezone: "America/Los_Angeles" });
    const inv = issueInvitation(EVENT_ID, GUEST_ID);
    renderToken(rsvpToken(inv));
    expect(screen.getByText(/Sep 1, 2026/)).toBeTruthy();
    expect(screen.getByText(/PDT/)).toBeTruthy();
  });
});

describe("canonical-state loop", () => {
  it("renders the response form for a valid open invitation", () => {
    const inv = issueInvitation(EVENT_ID, GUEST_ID);
    renderToken(rsvpToken(inv));
    expect(screen.getByText(/Test RSVP Summit/)).toBeTruthy();
    expect(screen.getByText(/Will you attend\?/i)).toBeTruthy();
  });

  it("a confirmed response updates the CANONICAL guest and records the response", () => {
    const inv = issueInvitation(EVENT_ID, GUEST_ID);
    renderToken(rsvpToken(inv));
    fireEvent.click(screen.getByLabelText(/Yes, I'll be there/i));
    fireEvent.click(screen.getByText(/Send response/i));
    // Canonical guest reflects the response (uncapped event → confirmed).
    expect(getGuests(EVENT_ID).find((g) => g.id === GUEST_ID)!.attendance.rsvp).toBe("confirmed");
    expect(getInvitation(GUEST_ID)!.respondedAt).toBeTruthy();
    expect(screen.getByRole('status').textContent).toMatch(/confirmed/i);
  });

  it("withdrawing returns the canonical guest to invited and releases plus-ones", () => {
    const inv = issueInvitation(EVENT_ID, GUEST_ID);
    renderToken(rsvpToken(inv));
    fireEvent.click(screen.getByText(/Withdraw/i));
    const g = getGuests(EVENT_ID).find((x) => x.id === GUEST_ID)!;
    expect(g.attendance.rsvp).toBe("invited");
    expect(g.attendance.plusOnes).toBe(0);
  });
});
