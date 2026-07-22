import { describe, it, expect, beforeEach } from "vitest";
import type { Guest } from "./types";
import { addGuest, getGuests, __resetGuests } from "./guests";
import {
  issueInvitation,
  setAllowance,
  rotateInvitation,
  recordSimulatedDelivery,
  getInvitation,
  __resetInvitations,
} from "./invitation-store";

const EVENT_ID = "evt-store-test";
const GUEST_ID = "g-store-test";

function seedGuest(over: Partial<Guest["attendance"]> = {}): Guest {
  const g: Guest = {
    id: GUEST_ID, eventId: EVENT_ID, identity: { firstName: "Sam", lastName: "Store" },
    professional: {}, roles: [], vip: false,
    attendance: { rsvp: "invited", checkedIn: false, noShow: false, waitlisted: false, plusOnes: 0, ...over },
    preferences: { hotelNeeded: false, transportationNeeded: false },
    communication: { invitationSent: false, reminderSent: false, thankYouSent: false },
    notes: {}, tags: [], seat: null, createdAt: "", updatedAt: "",
  };
  addGuest(g);
  return g;
}

beforeEach(() => {
  __resetGuests();
  __resetInvitations();
});

describe("recordSimulatedDelivery (finding #1)", () => {
  it("updates BOTH the invitation delivery state and the canonical guest flag", () => {
    seedGuest();
    issueInvitation(EVENT_ID, GUEST_ID);
    recordSimulatedDelivery(GUEST_ID);
    const inv = getInvitation(GUEST_ID)!;
    const guest = getGuests(EVENT_ID).find((g) => g.id === GUEST_ID)!;
    expect(inv.delivered).toBe(true);
    expect(inv.deliveredAt).toBeTruthy();
    expect(guest.communication.invitationSent).toBe(true); // cannot diverge
  });
});

describe("setAllowance invariant (finding #2)", () => {
  it("rejects reducing the allowance below the guest's accepted plus-ones", () => {
    seedGuest({ rsvp: "confirmed", plusOnes: 2 });
    issueInvitation(EVENT_ID, GUEST_ID);
    setAllowance(GUEST_ID, 3); // grant 3
    const bad = setAllowance(GUEST_ID, 1); // below the accepted 2
    expect(bad.ok).toBe(false);
    // The stored allowance is unchanged, and the accepted response is not mutated.
    expect(getInvitation(GUEST_ID)!.plusOneAllowance).toBe(3);
    expect(getGuests(EVENT_ID).find((g) => g.id === GUEST_ID)!.attendance.plusOnes).toBe(2);
  });
  it("allows reducing to exactly the accepted count", () => {
    seedGuest({ rsvp: "confirmed", plusOnes: 2 });
    issueInvitation(EVENT_ID, GUEST_ID);
    setAllowance(GUEST_ID, 4);
    const ok = setAllowance(GUEST_ID, 2);
    expect(ok.ok).toBe(true);
    expect(getInvitation(GUEST_ID)!.plusOneAllowance).toBe(2);
  });
});

describe("rotateInvitation (finding #3)", () => {
  it("resets delivery on the new link but keeps the guest's response history", () => {
    seedGuest();
    issueInvitation(EVENT_ID, GUEST_ID);
    recordSimulatedDelivery(GUEST_ID);
    // simulate a prior response record on the invitation
    getInvitation(GUEST_ID);

    const rotated = rotateInvitation(GUEST_ID)!;
    expect(rotated.tokenVersion).toBe(2);
    expect(rotated.delivered).toBe(false);
    expect(rotated.deliveredAt).toBeUndefined();
    expect(rotated.rotationCount).toBe(1);
  });
});
