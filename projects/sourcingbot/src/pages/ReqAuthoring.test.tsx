/**
 * @vitest-environment jsdom
 *
 * Authoring workflow: create → edit → autosave → reopen, plus the readiness
 * gate on opening a req for sourcing.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent, act } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AppShell } from "@/components/shell/AppShell";
import Workspace from "./Workspace";
import ReqAuthoring, { AUTOSAVE_MS } from "./ReqAuthoring";
import ReqDetail from "./ReqDetail";
import { __resetStore, createDraftReq, getState, saveReqDraft } from "@/lib/store";
import { updateReq } from "@/lib/req";
import { addRequirement, reviseBrief } from "@/lib/brief";
import { __resetIdCounter } from "@/lib/ids";

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/" element={<Workspace />} />
          <Route path="/reqs/:reqId/edit" element={<ReqAuthoring />} />
          <Route path="/reqs/:reqId" element={<ReqDetail />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

/** A draft with everything the readiness gate requires. */
function readyDraft() {
  const { req, brief } = createDraftReq();
  const r = updateReq(req, {
    code: "REQ-001", title: "Staff Engineer", team: "Infra", location: "Boston",
  });
  let b = reviseBrief(brief, { headline: "Platform engineers" });
  b = addRequirement(b, { label: "7+ years", kind: "required", weight: 5 });
  saveReqDraft(r, b);
  return r;
}

beforeEach(() => {
  localStorage.clear();
  __resetIdCounter();
  __resetStore();
});
afterEach(cleanup);

describe("creating a requisition", () => {
  it("offers a New requisition action on the workspace", () => {
    renderAt("/");
    expect(screen.getByRole("button", { name: /new requisition/i })).toBeTruthy();
  });

  it("creates a draft req and brief when clicked", () => {
    renderAt("/");
    fireEvent.click(screen.getByRole("button", { name: /new requisition/i }));
    expect(getState().reqs).toHaveLength(1);
    expect(getState().briefs).toHaveLength(1);
    expect(getState().reqs[0].status).toBe("draft");
  });
});

describe("the authoring surface", () => {
  it("renders progressive sections rather than one long form", () => {
    const req = readyDraft();
    renderAt(`/reqs/${req.id}/edit`);
    const tabs = screen.getAllByRole("tab");
    expect(tabs.length).toBe(7);
    // Only the active section's panel is visible.
    expect(screen.getByLabelText("Role title")).toBeTruthy();
    expect(screen.getByRole("tab", { name: /role basics/i }).getAttribute("aria-selected")).toBe("true");
  });

  it("switches sections without losing the req", () => {
    const req = readyDraft();
    renderAt(`/reqs/${req.id}/edit`);
    fireEvent.click(screen.getByRole("tab", { name: /job description/i }));
    expect(screen.getByLabelText(/full job description/i)).toBeTruthy();
  });

  it("shows a completeness ring and readiness verdict", () => {
    const req = readyDraft();
    renderAt(`/reqs/${req.id}/edit`);
    expect(screen.getByRole("status", { name: /percent complete/i })).toBeTruthy();
    expect(screen.getByText(/ready to source/i)).toBeTruthy();
  });

  it("handles an unknown req without crashing", () => {
    renderAt("/reqs/does-not-exist/edit");
    expect(screen.getByText(/requisition not found/i)).toBeTruthy();
  });
});

describe("autosave", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("persists an edit after the debounce", () => {
    const req = readyDraft();
    renderAt(`/reqs/${req.id}/edit`);

    fireEvent.change(screen.getByLabelText("Role title"), {
      target: { value: "Principal Engineer" },
    });
    expect(getState().reqs[0].title).toBe("Staff Engineer"); // not yet written

    act(() => {
      vi.advanceTimersByTime(AUTOSAVE_MS + 50);
    });
    expect(getState().reqs[0].title).toBe("Principal Engineer");
  });

  it("reports unsaved changes before the debounce fires", () => {
    const req = readyDraft();
    renderAt(`/reqs/${req.id}/edit`);
    fireEvent.change(screen.getByLabelText("Role title"), { target: { value: "Changed" } });
    expect(screen.getByText(/unsaved changes/i)).toBeTruthy();
  });

  it("reports all changes saved once written", () => {
    const req = readyDraft();
    renderAt(`/reqs/${req.id}/edit`);
    fireEvent.change(screen.getByLabelText("Role title"), { target: { value: "Changed" } });
    act(() => {
      vi.advanceTimersByTime(AUTOSAVE_MS + 50);
    });
    expect(screen.getByText(/all changes saved/i)).toBeTruthy();
  });

  it("Save draft writes immediately without waiting", () => {
    const req = readyDraft();
    renderAt(`/reqs/${req.id}/edit`);
    fireEvent.change(screen.getByLabelText("Role title"), { target: { value: "Immediate" } });
    fireEvent.click(screen.getByRole("button", { name: /save draft/i }));
    expect(getState().reqs[0].title).toBe("Immediate");
  });
});

describe("reopening a saved req", () => {
  it("loads previously authored values back into the form", () => {
    const req = readyDraft();
    saveReqDraft(updateReq(req, { jobDescription: "Persisted JD" }), undefined);
    renderAt(`/reqs/${req.id}/edit`);
    fireEvent.click(screen.getByRole("tab", { name: /job description/i }));
    expect((screen.getByLabelText(/full job description/i) as HTMLTextAreaElement).value).toBe(
      "Persisted JD",
    );
  });

  it("lists drafts on the workspace with their completeness", () => {
    readyDraft();
    renderAt("/");
    expect(screen.getByText("REQ-001")).toBeTruthy();
    expect(screen.getByText(/ready|in progress/i)).toBeTruthy();
  });
});

describe("opening for sourcing is gated on readiness", () => {
  it("is disabled while essentials are missing", () => {
    const { req } = createDraftReq();
    renderAt(`/reqs/${req.id}/edit`);
    const btn = screen.getByRole("button", { name: /open for sourcing/i }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it("is enabled once the req can discriminate between candidates", () => {
    const req = readyDraft();
    renderAt(`/reqs/${req.id}/edit`);
    const btn = screen.getByRole("button", { name: /open for sourcing/i }) as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
  });

  it("transitions the req to open when clicked", () => {
    const req = readyDraft();
    renderAt(`/reqs/${req.id}/edit`);
    fireEvent.click(screen.getByRole("button", { name: /open for sourcing/i }));
    expect(getState().reqs[0].status).toBe("open");
  });
});

describe("multi-req navigation", () => {
  it("keeps reqs independent", () => {
    const a = readyDraft();
    const b = createDraftReq().req;
    saveReqDraft(updateReq(b, { title: "Second Req" }), undefined);

    renderAt(`/reqs/${a.id}/edit`);
    expect((screen.getByLabelText("Role title") as HTMLInputElement).value).toBe("Staff Engineer");
    cleanup();

    renderAt(`/reqs/${b.id}/edit`);
    expect((screen.getByLabelText("Role title") as HTMLInputElement).value).toBe("Second Req");
  });
});
