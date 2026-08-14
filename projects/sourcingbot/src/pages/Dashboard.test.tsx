/**
 * @vitest-environment jsdom
 *
 * The dashboard as a command center. Assertions are about hierarchy, honesty
 * and action — that every section renders with the demo seed, that demo data is
 * labelled, and that each requisition offers the right next step.
 */
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, within, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AppShell } from "@/components/shell/AppShell";
import Dashboard from "./Dashboard";
import { __resetStore, __seedStore, getState } from "@/lib/store";
import { newReq, transition } from "@/lib/req";
import { newBrief } from "@/lib/brief";
import { newCandidate } from "@/lib/candidate";
import { newReqCandidate } from "@/lib/req-candidate";
import { startSession } from "@/lib/sourcing-session";
import { __resetIdCounter } from "@/lib/ids";

function renderHome() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/" element={<Dashboard />} />
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

describe("every section renders with the demo seed", () => {
  beforeEach(() => __seedStore());

  it("renders all seven sections", () => {
    renderHome();
    for (const heading of [
      "Working queue",
      "Requisitions",
      "Sourcing performance",
      "Talent intelligence",
      "Recent activity",
      "Talent pool",
    ]) {
      expect(screen.getAllByText(heading).length).toBeGreaterThan(0);
    }
  });

  it("shows all eight pulse stats, each linked", () => {
    renderHome();
    const labels = [
      "Open reqs", "Live sessions", "Candidates captured", "Needs review",
      "Close calls", "Reusable people", "Capture rate", "Avg fit score",
    ];
    const strip = screen.getByText("Open reqs").closest("ul") as HTMLElement;
    for (const l of labels) expect(within(strip).getByText(l)).toBeTruthy();
    expect(within(strip).getAllByRole("link")).toHaveLength(8);
  });

  it("populates every section rather than hiding behind empty states", () => {
    renderHome();
    expect(screen.queryByText(/nothing here yet/i)).toBeNull();
    expect(screen.queryByText(/no profiles reviewed yet/i)).toBeNull();
    expect(screen.queryByText(/not enough sessions/i)).toBeNull();
    expect(screen.queryByText(/nobody has been evaluated/i)).toBeNull();
  });
});

describe("8. demo data is labelled", () => {
  it("shows the demo banner when the workspace is seeded", () => {
    __seedStore();
    renderHome();
    expect(screen.getByText("Demo data")).toBeTruthy();
    expect(screen.getByText(/no real recruiter activity is shown/i)).toBeTruthy();
  });

  it("hides the banner once real data exists", () => {
    const req = transition(newReq({ code: "R1", title: "t", team: "t", location: "l" }), "open");
    const session = startSession({
      reqId: req.id, operator: "Real Person", acknowledgedPolicy: true, reqAcceptsSourcing: true,
    });
    __resetStore({
      reqs: [req], briefs: [newBrief({ reqId: req.id, headline: "h", seniority: "staff" })],
      candidates: [newCandidate({ fullName: "Real Candidate", origin: "referral" })],
      reqCandidates: [], sessions: [session],
    });
    renderHome();
    expect(screen.queryByText("Demo data")).toBeNull();
  });
});

describe("1. pulse", () => {
  it("reads calmly rather than showing bare zeros", () => {
    const req = transition(newReq({ code: "R1", title: "t", team: "t", location: "l" }), "open");
    __resetStore({ reqs: [req], briefs: [], candidates: [], reqCandidates: [], sessions: [] });
    renderHome();
    expect(screen.getByText("none running")).toBeTruthy();
  });
});

describe("3. requisitions board", () => {
  beforeEach(() => __seedStore());

  it("shows one card per active req with counts and readiness", () => {
    renderHome();
    expect(screen.getAllByText("REQ-014").length).toBeGreaterThan(0);
    expect(screen.getByText("Staff Platform Engineer")).toBeTruthy();
    expect(screen.getAllByText("Live").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Strong").length).toBeGreaterThan(0);
  });

  it("excludes closed reqs from the active board", () => {
    renderHome();
    const board = screen.getByText("Staff Platform Engineer").closest("ul") as HTMLElement;
    expect(within(board).queryByText("Senior iOS Engineer")).toBeNull();
  });

  it("marks a live session on its req", () => {
    renderHome();
    expect(screen.getByText("paused")).toBeTruthy();
    expect(screen.getByRole("link", { name: /resume session/i })).toBeTruthy();
  });

  it("offers a setup action for an unready draft", () => {
    renderHome();
    expect(screen.getAllByRole("link", { name: /finish setup/i }).length).toBeGreaterThan(0);
  });

  it("exposes readiness as an accessible progress bar", () => {
    renderHome();
    const bars = screen.getAllByRole("progressbar");
    expect(bars.length).toBeGreaterThan(0);
    expect(bars[0].getAttribute("aria-valuenow")).toBeTruthy();
  });
});

describe("5. sourcing performance", () => {
  beforeEach(() => __seedStore());

  it("shows the funnel with a capture rate", () => {
    renderHome();
    expect(screen.getByText("Reviewed")).toBeTruthy();
    expect(screen.getByText("Captured")).toBeTruthy();
    expect(screen.getByText("Skipped")).toBeTruthy();
    expect(screen.getAllByText("Close calls").length).toBeGreaterThan(0);
    expect(screen.getByText("capture rate")).toBeTruthy();
  });

  it("ranks a strongest and a weakest session", () => {
    renderHome();
    expect(screen.getByText("Strongest")).toBeTruthy();
    expect(screen.getByText("Weakest")).toBeTruthy();
  });

  it("keeps the weak-session caption neutral about cause", () => {
    renderHome();
    expect(screen.getByText(/Worth a look, not a verdict/i)).toBeTruthy();
  });
});

describe("4. talent intelligence", () => {
  beforeEach(() => __seedStore());

  it("offers four concentration dimensions", () => {
    renderHome();
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["Companies", "Locations", "Titles", "Skills"]);
  });

  it("lists people reusable across requisitions", () => {
    renderHome();
    expect(screen.getByText(/reusable across requisitions/i)).toBeTruthy();
    expect(screen.getAllByText(/\d reqs/).length).toBeGreaterThan(0);
  });
});

describe("7. talent pool preview", () => {
  beforeEach(() => __seedStore());

  it("shows a compact table with status and last activity", () => {
    renderHome();
    for (const col of ["Person", "Company", "Location", "Reqs", "Fit", "Status", "Last activity"]) {
      expect(screen.getByText(col)).toBeTruthy();
    }
  });

  it("is a preview — it links to the full workspace", () => {
    renderHome();
    expect(screen.getByRole("link", { name: /open the full talent workspace/i })).toBeTruthy();
  });

  it("does not render the whole pool", () => {
    renderHome();
    const table = screen.getByText("Person").closest("table") as HTMLElement;
    expect(within(table).getAllByRole("row").length).toBeLessThanOrEqual(7); // header + 6
  });
});

describe("hierarchy", () => {
  beforeEach(() => __seedStore());

  it("puts focus and requisitions before the talent pool", () => {
    renderHome();
    const focus = screen.getByText("Working queue");
    const allReqs = screen.getAllByText("Requisitions");
    const reqs = allReqs[allReqs.length - 1];
    const pool = screen.getByText("Talent pool");
    expect(focus.compareDocumentPosition(pool) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(reqs.compareDocumentPosition(pool) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("keeps the supervision boundary visible", () => {
    renderHome();
    expect(screen.getByText(/no unattended scraping/i)).toBeTruthy();
  });

  it("offers the full talent workspace separately", () => {
    renderHome();
    // Two routes lead there by design: the header nav and the pool footer.
    expect(screen.getAllByRole("link", { name: /talent workspace/i }).length).toBeGreaterThan(0);
  });
});

describe("the homepage is an operating surface, not a report", () => {
  /** A single open req with one strong, unactioned candidate. */
  function oneStrongCandidate() {
    const req = transition(
      newReq({ code: "REQ-500", title: "Staff Engineer", team: "Infra", location: "Boston" }),
      "open",
    );
    const brief = newBrief({ reqId: req.id, headline: "Platform", seniority: "staff" });
    const c = newCandidate({ fullName: "Wei Zhang", origin: "supervised-session" });
    const rc = {
      ...newReqCandidate({ reqId: req.id, candidateId: c.id, briefVersion: 1, by: "D" }),
      fitScore: 92,
    };
    __resetStore({
      reqs: [req], briefs: [{ ...brief, reqId: req.id }],
      candidates: [c], reqCandidates: [rc], sessions: [],
    });
    return { req, rc };
  }

  const queue = () => screen.getByRole("list", { name: /working queue/i });

  it("works a strong candidate in place — the record changes and the item leaves", () => {
    const { rc } = oneStrongCandidate();
    renderHome();
    expect(getState().reqCandidates[0].stage).toBe("identified");

    fireEvent.click(within(queue()).getByRole("button", { name: /move to reviewing/i }));

    expect(getState().reqCandidates.find((r) => r.id === rc.id)?.stage).toBe("reviewing");
    expect(screen.getByText(/1 item worked/i)).toBeTruthy();
  });

  it("rules a candidate out in place", () => {
    const { rc } = oneStrongCandidate();
    renderHome();
    fireEvent.click(within(queue()).getByRole("button", { name: /not a fit/i }));
    expect(getState().reqCandidates.find((r) => r.id === rc.id)?.stage).toBe("rejected");
  });

  it("records who moved it and why, through the domain", () => {
    oneStrongCandidate();
    renderHome();
    fireEvent.click(within(queue()).getByRole("button", { name: /move to reviewing/i }));
    const rc = getState().reqCandidates[0];
    const last = rc.history[rc.history.length - 1];
    expect(last.by).toBe("You");
    expect(last.reason).toMatch(/working queue/i);
  });

  it("offers no snooze or dismiss — items leave only when work happens", () => {
    oneStrongCandidate();
    renderHome();
    expect(screen.queryByRole("button", { name: /snooze|dismiss|hide/i })).toBeNull();
  });

  it("is keyboard-workable", () => {
    oneStrongCandidate();
    renderHome();
    const list = queue();
    expect(list.getAttribute("tabindex")).toBe("0");
    fireEvent.keyDown(list, { key: "Enter" });
    expect(screen.getByText(/item worked/i)).toBeTruthy();
  });

  /** An open req with an empty pipeline — surfaces "Start sourcing". */
  function thinPipeline() {
    const req = transition(
      newReq({ code: "REQ-600", title: "Security Engineer", team: "Sec", location: "NY" }),
      "open",
    );
    __resetStore({
      reqs: [req],
      briefs: [{ ...newBrief({ reqId: req.id, headline: "Sec", seniority: "senior" }), reqId: req.id }],
      candidates: [], reqCandidates: [], sessions: [],
    });
    return req;
  }

  it("starts a supervised session inline, without leaving home", () => {
    thinPipeline();
    renderHome();
    fireEvent.click(within(queue()).getByRole("button", { name: /start sourcing/i }));
    expect(screen.getByLabelText(/your name/i)).toBeTruthy();
    expect(screen.getByText(/supervision policy/i)).toBeTruthy();
  });

  it("the inline gate still refuses without a name and acknowledgement", () => {
    thinPipeline();
    renderHome();
    fireEvent.click(within(queue()).getByRole("button", { name: /start sourcing/i }));
    const btn = () => screen.getByRole("button", { name: /^start session$/i }) as HTMLButtonElement;
    expect(btn().disabled).toBe(true);

    fireEvent.change(screen.getByLabelText(/your name/i), { target: { value: "Dana" } });
    expect(btn().disabled).toBe(true);   // named, but not acknowledged

    fireEvent.click(screen.getByRole("checkbox"));
    expect(btn().disabled).toBe(false);
  });

  it("creates a real supervised session from the homepage", () => {
    thinPipeline();
    renderHome();
    fireEvent.click(within(queue()).getByRole("button", { name: /start sourcing/i }));
    fireEvent.change(screen.getByLabelText(/your name/i), { target: { value: "Dana Whitfield" } });
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: /^start session$/i }));

    const created = getState().sessions[0];
    expect(created.operator).toBe("Dana Whitfield");
    expect(created.acknowledgedPolicy).toBe(true);
    expect(created.status).toBe("in-progress");
  });
});
