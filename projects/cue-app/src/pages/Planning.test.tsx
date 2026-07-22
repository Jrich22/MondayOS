/**
 * @vitest-environment jsdom
 *
 * Regression (finding #1): an unknown / stale event URL must fail safely and
 * render the "Event not found" UI instead of throwing. Previously the page
 * passed an invalid `{ id } as never` fallback into usePlan → emptyPlan, which
 * dereferenced `startsAt` and crashed before the not-found UI could render.
 */
import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import Planning from "./Planning";
import { getEvent } from "@/lib/store";
import { seedEvents } from "@/lib/data";

afterEach(cleanup);

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/events/:id/planning" element={<Planning />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("Planning route safety", () => {
  it("renders 'Event not found' for an unknown event id without throwing", () => {
    expect(() => renderAt("/events/does-not-exist/planning")).not.toThrow();
    expect(screen.getByText(/event not found/i)).toBeTruthy();
  });

  it("renders the workspace for a valid seeded event", () => {
    const id = seedEvents[0].id;
    expect(getEvent(id)).toBeTruthy();
    renderAt(`/events/${id}/planning`);
    expect(screen.getByText(/Event Planning/i)).toBeTruthy();
    expect(screen.getByText(/Operational readiness/i)).toBeTruthy();
  });
});
