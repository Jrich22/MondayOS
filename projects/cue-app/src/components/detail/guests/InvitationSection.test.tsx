/**
 * @vitest-environment jsdom
 *
 * Invite & RSVP slice 1 corrections — organizer invitation controls:
 * honest clipboard/delivery semantics and RSVP-enablement guard.
 */
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import { InvitationSection } from "./InvitationSection";
import type { CueEvent, Guest } from "@/lib/types";
import { addGuest, getGuests, __resetGuests } from "@/lib/guests";
import { issueInvitation, getInvitation, __resetInvitations } from "@/lib/invitation-store";

const EVENT_ID = "evt-inv-sec";
const GUEST_ID = "g-inv-sec";

function ev(rsvpEnabled = true): CueEvent {
  return {
    id: EVENT_ID, title: "Sec Test", classification: "portfolio", summary: "",
    startsAt: "2026-09-01T18:00:00Z", timezone: "America/Los_Angeles", status: "upcoming",
    venue: "HQ", city: "SF", host: "Dana",
    capacity: { maxAttendees: null, rsvpEnabled, waitlistEnabled: false },
    confirmedGuests: 0, invitedGuests: 0, branding: { theme: "default" }, portfolio: [], tags: [],
    createdAt: "2026-08-01T00:00:00Z",
  };
}
function guest(): Guest {
  return {
    id: GUEST_ID, eventId: EVENT_ID, identity: { firstName: "Sam", lastName: "Sec" },
    professional: {}, roles: [], vip: false,
    attendance: { rsvp: "invited", checkedIn: false, noShow: false, waitlisted: false, plusOnes: 0 },
    preferences: { hotelNeeded: false, transportationNeeded: false },
    communication: { invitationSent: false, reminderSent: false, thankYouSent: false },
    notes: {}, tags: [], seat: null, createdAt: "", updatedAt: "",
  };
}

function setClipboard(writeText: (t: string) => Promise<void>) {
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
}

beforeEach(() => { __resetGuests(); __resetInvitations(); addGuest(guest()); });
afterEach(cleanup);

describe("RSVP enablement guard (finding #5)", () => {
  it("blocks issuance and explains when RSVP is disabled", () => {
    render(<InvitationSection guest={guest()} event={ev(false)} />);
    expect(screen.getByText(/RSVP is turned off/i)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Issue invitation/i })).toBeNull();
  });
});

describe("clipboard / delivery honesty (finding #1)", () => {
  it("does NOT show Copied or mark delivered when the clipboard write fails", async () => {
    setClipboard(() => Promise.reject(new Error("blocked")));
    issueInvitation(EVENT_ID, GUEST_ID);
    render(<InvitationSection guest={guest()} event={ev()} />);
    fireEvent.click(screen.getByText("Copy link"));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/couldn't copy/i));
    expect(screen.queryByText(/Copied/i)).toBeNull();
    expect(getInvitation(GUEST_ID)!.delivered).toBe(false); // not marked delivered
    expect(getGuests(EVENT_ID).find((g) => g.id === GUEST_ID)!.communication.invitationSent).toBe(false);
  });

  it("marks delivery on BOTH records when the clipboard write succeeds", async () => {
    setClipboard(() => Promise.resolve());
    issueInvitation(EVENT_ID, GUEST_ID);
    render(<InvitationSection guest={guest()} event={ev()} />);
    fireEvent.click(screen.getByText("Copy link"));
    await waitFor(() => expect(getInvitation(GUEST_ID)!.delivered).toBe(true));
    expect(getGuests(EVENT_ID).find((g) => g.id === GUEST_ID)!.communication.invitationSent).toBe(true);
  });

  it("'Mark delivered' records delivery even without a working clipboard", () => {
    issueInvitation(EVENT_ID, GUEST_ID);
    render(<InvitationSection guest={guest()} event={ev()} />);
    fireEvent.click(screen.getByText(/Mark delivered/i));
    expect(getInvitation(GUEST_ID)!.delivered).toBe(true);
    expect(getGuests(EVENT_ID).find((g) => g.id === GUEST_ID)!.communication.invitationSent).toBe(true);
  });
});
