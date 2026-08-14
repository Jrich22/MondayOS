/**
 * @vitest-environment jsdom
 *
 * The Candidate Workspace as a command center, not a table.
 *
 * The assertions are deliberately about *hierarchy and action*: that focus
 * comes before the pool, that every stat links somewhere, and that
 * concentration filters rather than decorates.
 */
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AppShell } from "@/components/shell/AppShell";
import CandidateWorkspace from "./CandidateWorkspace";
import { __resetStore, __seedStore, getState } from "@/lib/store";
import { newCandidate } from "@/lib/candidate";
import { newReq, transition } from "@/lib/req";
import { newBrief } from "@/lib/brief";
import { newReqCandidate } from "@/lib/req-candidate";
import { recordSkip, startSession } from "@/lib/sourcing-session";
import { __resetIdCounter } from "@/lib/ids";

function renderHome() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/" element={<CandidateWorkspace />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  localStorage.clear();
  __resetIdCounter();
  __resetStore();
});
afterEach(cleanup);

describe("the workspace is the home surface", () => {
  it("is reachable at / and titled for talent", () => {
    __seedStore();
    renderHome();
    expect(screen.getByRole("heading", { name: "Talent", level: 1 })).toBeTruthy();
  });

  it("shows a product-level empty state before anything exists", () => {
    renderHome();
    expect(screen.getByText(/nothing here yet/i)).toBeTruthy();
  });

  it("keeps the supervision boundary visible", () => {
    __seedStore();
    renderHome();
    expect(screen.getByText(/no unattended scraping/i)).toBeTruthy();
  });
});

describe("1. pulse", () => {
  it("shows the six reflexive counts", () => {
    __seedStore();
    renderHome();
    for (const label of [
      "Open reqs", "Live sessions", "Added today", "Close calls", "Reusable people", "Needs review",
    ]) {
      expect(screen.getByText(label)).toBeTruthy();
    }
  });

  it("makes every stat a link — no number without a destination", () => {
    __seedStore();
    renderHome();
    const strip = screen.getByText("Open reqs").closest("ul");
    const links = within(strip as HTMLElement).getAllByRole("link");
    expect(links).toHaveLength(6);
    for (const l of links) expect(l.getAttribute("href")).toBeTruthy();
  });

  it("reads calmly rather than showing zeros everywhere", () => {
    const req = transition(newReq({ code: "R1", title: "t", team: "t", location: "l" }), "open");
    __resetStore({ reqs: [req], briefs: [], candidates: [], reqCandidates: [], sessions: [] });
    renderHome();
    expect(screen.getByText("none running")).toBeTruthy();
  });
});

describe("2. recommended focus", () => {
  it("leads the page — it appears before the talent pool", () => {
    __seedStore();
    renderHome();
    const focus = screen.getByText("Recommended focus");
    const pool = screen.getByText("Talent pool");
    expect(focus.compareDocumentPosition(pool) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("surfaces a thin pipeline with a reason and an action", () => {
    const req = transition(newReq({ code: "REQ-014", title: "Staff Engineer", team: "Infra", location: "Boston" }), "open");
    __resetStore({ reqs: [req], briefs: [], candidates: [], reqCandidates: [], sessions: [] });
    renderHome();
    expect(screen.getByText("Thin pipeline")).toBeTruthy();
    expect(screen.getByText(/has nobody in the pipeline/i)).toBeTruthy();
    expect(screen.getByRole("link", { name: /start sourcing/i })).toBeTruthy();
  });

  it("surfaces a close call worth revisiting", () => {
    const req = transition(newReq({ code: "REQ-014", title: "t", team: "t", location: "l" }), "open");
    const s = recordSkip(
      startSession({ reqId: req.id, operator: "D", acknowledgedPolicy: true, reqAcceptsSourcing: true }),
      { name: "Tomás Beckett", reason: "Too junior", closeCall: true },
    );
    __resetStore({ reqs: [req], briefs: [], candidates: [], reqCandidates: [], sessions: [s] });
    renderHome();
    // Appears in focus AND the activity feed — both correct, so scope to focus.
    const badge = screen.getByText("Close call");
    const row = badge.closest("li") as HTMLElement;
    expect(within(row).getByText("Tomás Beckett")).toBeTruthy();
    expect(within(row).getByRole("link", { name: /revisit in session/i })).toBeTruthy();
  });

  it("says so plainly when nothing needs attention", () => {
    const c = newCandidate({ fullName: "Priya", origin: "referral" });
    __resetStore({ reqs: [], briefs: [], candidates: [c], reqCandidates: [], sessions: [] });
    renderHome();
    expect(screen.getByText(/nothing needs you right now/i)).toBeTruthy();
  });
});

describe("3. talent intelligence", () => {
  it("offers four dimensions and defaults to companies", () => {
    __seedStore();
    renderHome();
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["Companies", "Locations", "Titles", "Skills"]);
    expect(tabs[0].getAttribute("aria-selected")).toBe("true");
  });

  it("switches dimension", () => {
    __seedStore();
    renderHome();
    fireEvent.click(screen.getByRole("tab", { name: "Skills" }));
    expect(screen.getByRole("tab", { name: "Skills" }).getAttribute("aria-selected")).toBe("true");
  });

  it("clicking a row FILTERS the pool rather than only displaying a number", () => {
    __seedStore();
    renderHome();
    const row = screen.getAllByRole("button").find((b) => b.textContent?.includes("Northwind"));
    fireEvent.click(row as HTMLElement);
    expect(screen.getByText(/filtered to/i)).toBeTruthy();
  });

  it("the filter can be cleared", () => {
    __seedStore();
    renderHome();
    const row = screen.getAllByRole("button").find((b) => b.textContent?.includes("Northwind"));
    fireEvent.click(row as HTMLElement);
    fireEvent.click(screen.getAllByRole("button", { name: /^clear$/i })[0]);
    expect(screen.queryByText(/filtered to/i)).toBeNull();
  });
});

describe("4. activity", () => {
  it("shows one merged feed", () => {
    __seedStore();
    renderHome();
    expect(screen.getByText("Recent activity")).toBeTruthy();
  });
});

describe("5. talent pool", () => {
  it("exists but does not dominate — it is the last section", () => {
    __seedStore();
    renderHome();
    const sections = ["Recommended focus", "Talent intelligence", "Talent pool"];
    const positions = sections.map((s) => screen.getByText(s));
    expect(positions[0].compareDocumentPosition(positions[2]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(positions[1].compareDocumentPosition(positions[2]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("offers search, saved views and CSV export", () => {
    __seedStore();
    renderHome();
    expect(screen.getByLabelText(/search the talent pool/i)).toBeTruthy();
    const views = screen.getByRole("group", { name: /saved views/i });
    expect(within(views).getAllByRole("button").length).toBe(5);
    expect(screen.getByRole("button", { name: /export csv/i })).toBeTruthy();
  });

  it("searches the pool", () => {
    __seedStore();
    renderHome();
    fireEvent.change(screen.getByLabelText(/search the talent pool/i), { target: { value: "zzzzz" } });
    expect(screen.getByText(/no matches/i)).toBeTruthy();
  });

  it("the Reusable view shows only people on more than one req", () => {
    const c = newCandidate({ fullName: "Priya Raman", origin: "referral" });
    const other = newCandidate({ fullName: "Solo Person", origin: "referral" });
    const req1 = transition(newReq({ code: "R1", title: "t", team: "t", location: "l" }), "open");
    const req2 = transition(newReq({ code: "R2", title: "t", team: "t", location: "l" }), "open");
    __resetStore({
      reqs: [req1, req2],
      briefs: [newBrief({ reqId: req1.id, headline: "h", seniority: "staff" })],
      candidates: [c, other],
      reqCandidates: [
        newReqCandidate({ reqId: req1.id, candidateId: c.id, briefVersion: 1, by: "D" }),
        newReqCandidate({ reqId: req2.id, candidateId: c.id, briefVersion: 1, by: "D" }),
      ],
      sessions: [],
    });
    renderHome();

    expect(screen.getByText("Solo Person")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Reusable" }));
    expect(screen.queryByText("Solo Person")).toBeNull();
    expect(screen.getAllByText("Priya Raman").length).toBeGreaterThan(0);
  });

  it("links each person to their profile", () => {
    __seedStore();
    renderHome();
    const link = screen.getAllByRole("link").find((l) => l.getAttribute("href")?.startsWith("/candidates/"));
    expect(link).toBeTruthy();
  });

  it("does not render the whole pool up front", () => {
    const many = Array.from({ length: 20 }, (_, i) =>
      newCandidate({ fullName: `Person ${i}`, origin: "referral" }),
    );
    __resetStore({ reqs: [], briefs: [], candidates: many, reqCandidates: [], sessions: [] });
    renderHome();
    expect(screen.queryByText("Person 19")).toBeNull();
    expect(screen.getByRole("button", { name: /show all 20/i })).toBeTruthy();
  });

  it("expands on request", () => {
    const many = Array.from({ length: 20 }, (_, i) =>
      newCandidate({ fullName: `Person ${i}`, origin: "referral" }),
    );
    __resetStore({ reqs: [], briefs: [], candidates: many, reqCandidates: [], sessions: [] });
    renderHome();
    fireEvent.click(screen.getByRole("button", { name: /show all 20/i }));
    expect(screen.getByText("Person 19")).toBeTruthy();
  });
});

describe("no duplicated business logic", () => {
  it("reads from the existing store collections only", () => {
    __seedStore();
    renderHome();
    expect(Object.keys(getState()).sort()).toEqual([
      "briefs", "candidates", "reqCandidates", "reqs", "sessions",
    ]);
  });
});
