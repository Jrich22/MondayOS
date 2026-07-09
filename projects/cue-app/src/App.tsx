import { HashRouter, Routes, Route } from "react-router-dom";
import { Sidebar } from "@/components/shell/Sidebar";
import { Topbar } from "@/components/shell/Topbar";
import { Dashboard } from "@/pages/Dashboard";
import { CreateEvent } from "@/pages/CreateEvent";
import { EventDetail } from "@/pages/EventDetail";
import { RollCall } from "@/pages/RollCall";
import { ComingSoon } from "@/pages/ComingSoon";

/**
 * App shell (foundation for TASK-0030) + routing. HashRouter keeps the SPA
 * self-contained with no server-side route config — appropriate for the
 * static, mock-data MVP. The dashboard is the index route; later surfaces are
 * routed to tasteful placeholders until their tasks land.
 *
 * The Roll Call Command Center (TASK-0040) is deliberately routed *outside* the
 * shell: it is a full-viewport operational mode with its own top bar, so it must
 * not sit inside the sidebar + width-capped main content area.
 */
export default function App() {
  return (
    <HashRouter>
      <Routes>
        <Route path="/events/:id/rollcall" element={<RollCall />} />
        <Route path="/*" element={<ShellLayout />} />
      </Routes>
    </HashRouter>
  );
}

/** The standard chrome (sidebar + top bar + width-capped content). */
function ShellLayout() {
  return (
    <div className="flex min-h-screen">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar />
        <main className="mx-auto w-full max-w-7xl flex-1 px-5 py-8 sm:px-8">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/events/new" element={<CreateEvent />} />
            <Route path="/events/:id" element={<EventDetail />} />
            <Route path="/events" element={<ComingSoon title="Events" task="TASK-0034" />} />
            <Route path="/guests" element={<ComingSoon title="Guests" task="TASK-0035" />} />
            <Route path="/portfolio" element={<ComingSoon title="Portfolio" task="TASK-0026" />} />
            <Route path="/assistant" element={<ComingSoon title="AI Assistant" task="TASK-0036" />} />
            <Route path="*" element={<ComingSoon title="Not found" task="—" />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}
