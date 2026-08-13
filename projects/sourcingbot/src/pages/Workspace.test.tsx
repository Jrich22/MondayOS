/**
 * @vitest-environment jsdom
 *
 * Shell and surface smoke tests. The load-bearing assertion is the last one:
 * the same person renders on two requisitions with two different stages, which
 * is the model rule made visible in the UI.
 */
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AppShell } from "@/components/shell/AppShell";
import Workspace from "./Workspace";
import ReqDetail from "./ReqDetail";
import Candidates from "./Candidates";
import CandidateProfile from "./CandidateProfile";
import { __seedStore, __resetStore } from "@/lib/store";

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/" element={<Workspace />} />
          <Route path="/reqs/:reqId" element={<ReqDetail />} />
          <Route path="/candidates" element={<Candidates />} />
          <Route path="/candidates/:candidateId" element={<CandidateProfile />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  localStorage.clear();
  __seedStore();
});
afterEach(cleanup);

describe("application shell", () => {
  it("renders the product identity and navigation", () => {
    renderAt("/");
    expect(screen.getAllByText("sourcingBOT").length).toBeGreaterThan(0);
    expect(screen.getByText("Workspace")).toBeTruthy();
    expect(screen.getByText("Talent")).toBeTruthy();
  });

  it("always shows the supervision boundary", () => {
    renderAt("/");
    expect(screen.getByText(/human-initiated/i)).toBeTruthy();
    expect(screen.getByText(/no unattended scraping/i)).toBeTruthy();
  });
});

describe("Req Workspace", () => {
  it("lists requisitions with codes", () => {
    renderAt("/");
    expect(screen.getByText("Req Workspace")).toBeTruthy();
    expect(screen.getByText("REQ-014")).toBeTruthy();
    expect(screen.getByText("Staff Platform Engineer")).toBeTruthy();
  });

  it("shows an empty state when there is no work", () => {
    __resetStore();
    renderAt("/");
    expect(screen.getByText(/No requisitions yet/i)).toBeTruthy();
  });
});

describe("Req detail", () => {
  it("renders the brief and its pipeline", () => {
    renderAt("/reqs/req_infra");
    expect(screen.getByText("Pipeline")).toBeTruthy();
    expect(screen.getByText(/multi-tenant infrastructure at scale/i)).toBeTruthy();
    expect(screen.getByText("Priya Raman")).toBeTruthy();
  });

  it("handles an unknown requisition without crashing", () => {
    renderAt("/reqs/does-not-exist");
    expect(screen.getByText(/Requisition not found/i)).toBeTruthy();
  });
});

describe("Talent directory", () => {
  it("lists each person once with a requisition count", () => {
    renderAt("/candidates");
    expect(screen.getAllByText("Priya Raman")).toHaveLength(1);
    expect(screen.getByText("2 requisitions")).toBeTruthy();
  });

  it("surfaces talent concentration", () => {
    renderAt("/candidates");
    expect(screen.getByText(/Talent concentration/i)).toBeTruthy();
  });
});

describe("the persistent-person model, end to end", () => {
  it("shows one person across two reqs with independent stages", () => {
    renderAt("/candidates/cand_priya");

    expect(screen.getByText("REQ-014")).toBeTruthy();
    expect(screen.getByText("REQ-018")).toBeTruthy();
    expect(screen.getByText("responded")).toBeTruthy();
    expect(screen.getByText("rejected")).toBeTruthy();
  });

  it("scores the same person differently per requisition", () => {
    renderAt("/candidates/cand_priya");
    expect(screen.getByText("79")).toBeTruthy();
    expect(screen.getByText("0")).toBeTruthy();
  });
});
