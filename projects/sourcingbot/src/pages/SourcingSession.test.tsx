/**
 * @vitest-environment jsdom
 *
 * Session workflow: the acknowledgement gate, capture, skip, pause/resume, and
 * the persistent-person rule end to end through the UI.
 */
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AppShell } from "@/components/shell/AppShell";
import SourcingSession from "./SourcingSession";
import { __resetStore, addCandidate, addSession, getState } from "@/lib/store";
import { newReq, transition } from "@/lib/req";
import { addRequirement, newBrief } from "@/lib/brief";
import { newCandidate } from "@/lib/candidate";
import { startSession } from "@/lib/linkedin";
import { __resetIdCounter } from "@/lib/ids";

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/reqs/:reqId/session" element={<SourcingSession />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

/** An open req with a brief that can discriminate. */
function openReq() {
  const req = transition(
    newReq({ code: "REQ-014", title: "Staff Engineer", team: "Infra", location: "Boston" }),
    "open",
  );
  const brief = addRequirement(
    newBrief({ reqId: req.id, headline: "Platform engineers", seniority: "staff" }),
    { label: "7+ years", kind: "required", weight: 5 },
  );
  __resetStore({ reqs: [req], briefs: [{ ...brief, reqId: req.id }] });
  return req;
}

function live(reqId: string) {
  const s = startSession({
    reqId, operator: "Dana Whitfield", acknowledgedPolicy: true, reqAcceptsSourcing: true,
  });
  addSession(s);
  return s;
}

beforeEach(() => {
  localStorage.clear();
  __resetIdCounter();
  __resetStore();
});
afterEach(cleanup);

describe("the supervision gate", () => {
  it("shows the policy and requires acknowledgement before starting", () => {
    const req = openReq();
    renderAt(`/reqs/${req.id}/session`);
    expect(screen.getByText(/supervision policy/i)).toBeTruthy();
    expect(screen.getByText(/personally supervising/i)).toBeTruthy();
    const start = screen.getByRole("button", { name: /start session/i }) as HTMLButtonElement;
    expect(start.disabled).toBe(true);
  });

  it("stays disabled with a name but no acknowledgement", () => {
    const req = openReq();
    renderAt(`/reqs/${req.id}/session`);
    fireEvent.change(screen.getByLabelText(/your name/i), { target: { value: "Dana" } });
    expect((screen.getByRole("button", { name: /start session/i }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("stays disabled with acknowledgement but no name", () => {
    const req = openReq();
    renderAt(`/reqs/${req.id}/session`);
    fireEvent.click(screen.getByRole("checkbox"));
    expect((screen.getByRole("button", { name: /start session/i }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("starts once both are given", () => {
    const req = openReq();
    renderAt(`/reqs/${req.id}/session`);
    fireEvent.change(screen.getByLabelText(/your name/i), { target: { value: "Dana Whitfield" } });
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: /start session/i }));

    expect(getState().sessions).toHaveLength(1);
    expect(getState().sessions[0].operator).toBe("Dana Whitfield");
    expect(getState().sessions[0].acknowledgedPolicy).toBe(true);
  });

  it("refuses to start on a req that is not open for sourcing", () => {
    const draft = newReq({ code: "REQ-1", title: "x", team: "t", location: "l" });
    __resetStore({ reqs: [draft], briefs: [] });
    renderAt(`/reqs/${draft.id}/session`);
    expect(screen.getByText(/open it for sourcing/i)).toBeTruthy();
    expect((screen.getByRole("button", { name: /start session/i }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("always states that sourcingBOT does not browse", () => {
    const req = openReq();
    renderAt(`/reqs/${req.id}/session`);
    expect(screen.getByText(/does not open, fetch, or parse any profile/i)).toBeTruthy();
  });

  it("handles an unknown req without crashing", () => {
    renderAt("/reqs/nope/session");
    expect(screen.getByText(/requisition not found/i)).toBeTruthy();
  });
});

describe("capturing during a session", () => {
  it("creates a person and an evaluation", () => {
    const req = openReq();
    live(req.id);
    renderAt(`/reqs/${req.id}/session`);

    fireEvent.change(screen.getByLabelText(/full name/i), { target: { value: "Priya Raman" } });
    fireEvent.click(screen.getByRole("button", { name: /add to requisition/i }));

    const s = getState();
    expect(s.candidates).toHaveLength(1);
    expect(s.candidates[0].fullName).toBe("Priya Raman");
    expect(s.reqCandidates).toHaveLength(1);
    expect(s.reqCandidates[0].reqId).toBe(req.id);
    expect(s.sessions[0].candidatesAdded).toBe(1);
  });

  it("refuses an unnamed capture", () => {
    const req = openReq();
    live(req.id);
    renderAt(`/reqs/${req.id}/session`);
    fireEvent.click(screen.getByRole("button", { name: /add to requisition/i }));
    expect(screen.getByRole("alert").textContent).toMatch(/needs a name/i);
    expect(getState().candidates).toHaveLength(0);
  });

  it("warns about a possible duplicate and offers reuse", () => {
    const req = openReq();
    const existing = newCandidate({ fullName: "Priya Raman", email: "p@example.com", origin: "referral" });
    addCandidate(existing);
    live(req.id);
    renderAt(`/reqs/${req.id}/session`);

    fireEvent.change(screen.getByLabelText(/full name/i), { target: { value: "Priya Raman" } });
    fireEvent.change(screen.getByLabelText(/email/i), { target: { value: "p@example.com" } });

    const alert = screen.getByRole("alert");
    expect(within(alert).getByText(/possible duplicate/i)).toBeTruthy();
    expect(within(alert).getByRole("button", { name: /reuse/i })).toBeTruthy();
  });

  it("reuses the existing person rather than creating a second one", () => {
    const req = openReq();
    const existing = newCandidate({ fullName: "Priya Raman", email: "p@example.com", origin: "referral" });
    addCandidate(existing);
    live(req.id);
    renderAt(`/reqs/${req.id}/session`);

    fireEvent.change(screen.getByLabelText(/full name/i), { target: { value: "Priya Raman" } });
    fireEvent.change(screen.getByLabelText(/email/i), { target: { value: "p@example.com" } });
    fireEvent.click(within(screen.getByRole("alert")).getByRole("button", { name: /reuse/i }));
    fireEvent.click(screen.getByRole("button", { name: /add existing person to req/i }));

    // One person, one new evaluation — the whole point of the persistent pool.
    expect(getState().candidates).toHaveLength(1);
    expect(getState().candidates[0].id).toBe(existing.id);
    expect(getState().reqCandidates).toHaveLength(1);
    expect(getState().reqCandidates[0].candidateId).toBe(existing.id);
  });

  it("records a skip with a close-call flag and creates no person", () => {
    const req = openReq();
    live(req.id);
    renderAt(`/reqs/${req.id}/session`);

    fireEvent.change(screen.getByLabelText(/full name/i), { target: { value: "Tomás Beckett" } });
    fireEvent.change(screen.getByLabelText(/skip reason/i), { target: { value: "Too junior" } });
    fireEvent.click(screen.getByLabelText(/close call/i));
    fireEvent.click(screen.getByRole("button", { name: /^skip$/i }));

    const s = getState().sessions[0];
    expect(s.skipped).toHaveLength(1);
    expect(s.skipped?.[0]).toMatchObject({ name: "Tomás Beckett", closeCall: true });
    expect(getState().candidates).toHaveLength(0);
  });

  it("shows live session counts", () => {
    const req = openReq();
    live(req.id);
    renderAt(`/reqs/${req.id}/session`);
    fireEvent.change(screen.getByLabelText(/full name/i), { target: { value: "Priya Raman" } });
    fireEvent.click(screen.getByRole("button", { name: /add to requisition/i }));

    expect(screen.getByText("Captured")).toBeTruthy();
    expect(screen.getByText("Capture rate")).toBeTruthy();
  });
});

describe("pause and resume", () => {
  it("pauses and suspends capture", () => {
    const req = openReq();
    live(req.id);
    renderAt(`/reqs/${req.id}/session`);

    fireEvent.click(screen.getByRole("button", { name: /^pause$/i }));
    expect(getState().sessions[0].status).toBe("paused");
    expect(screen.getByText(/session paused/i)).toBeTruthy();
    expect(screen.queryByLabelText(/full name/i)).toBeNull();
  });

  it("resumes and restores capture", () => {
    const req = openReq();
    live(req.id);
    renderAt(`/reqs/${req.id}/session`);

    fireEvent.click(screen.getByRole("button", { name: /^pause$/i }));
    fireEvent.click(screen.getByRole("button", { name: /^resume$/i }));
    expect(getState().sessions[0].status).toBe("in-progress");
    expect(screen.getByLabelText(/full name/i)).toBeTruthy();
  });

  it("completes the session and returns to the gate", () => {
    const req = openReq();
    live(req.id);
    renderAt(`/reqs/${req.id}/session`);

    fireEvent.click(screen.getByRole("button", { name: /^complete$/i }));
    expect(getState().sessions[0].status).toBe("ended");
    expect(screen.getByRole("button", { name: /start session/i })).toBeTruthy();
  });
});

describe("session history", () => {
  it("lists a completed session with its counts", () => {
    const req = openReq();
    live(req.id);
    renderAt(`/reqs/${req.id}/session`);
    fireEvent.change(screen.getByLabelText(/full name/i), { target: { value: "Priya Raman" } });
    fireEvent.click(screen.getByRole("button", { name: /add to requisition/i }));
    fireEvent.click(screen.getByRole("button", { name: /^complete$/i }));

    expect(screen.getByText(/session history/i)).toBeTruthy();
    expect(screen.getByText(/1 captured/i)).toBeTruthy();
  });
});
