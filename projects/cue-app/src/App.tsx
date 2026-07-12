import { HashRouter, Routes, Route } from "react-router-dom";
import { Sidebar } from "@/components/shell/Sidebar";
import { Topbar } from "@/components/shell/Topbar";
import { MissionControl } from "@/pages/MissionControl";
import { Dashboard } from "@/pages/Dashboard";
import { CreateEvent } from "@/pages/CreateEvent";
import { EventDetail } from "@/pages/EventDetail";
import { RollCall } from "@/pages/RollCall";
import { CheckIn } from "@/pages/CheckIn";
import { People } from "@/pages/People";
import { PersonProfile } from "@/pages/PersonProfile";
import { ComingSoon } from "@/pages/ComingSoon";

/**
 * App shell (foundation for TASK-0030) + routing. HashRouter keeps the SPA
 * self-contained with no server-side route config — appropriate for the
 * static, mock-data MVP. The dashboard is the index route; later surfaces are
 * routed to tasteful placeholders until their tasks land.
 *
 * Mission Control (TASK-0042) is the index route — the organizer's home screen.
 * The Events catalog (formerly the index) now lives at /events.
 *
 * The Roll Call Command Center (TASK-0040) and the Check-In Kiosk (TASK-0045)
 * are deliberately routed *outside* the shell: they are full-viewport operational
 * modes with their own top bar, so they must not sit inside the sidebar +
 * width-capped main content area.
 */
export default function App() {
  return (
    <HashRouter>
      <Routes>
        <Route path="/events/:id/rollcall" element={<RollCall />} />
        <Route path="/events/:id/checkin" element={<CheckIn />} />
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
            <Route path="/" element={<MissionControl />} />
            <Route path="/events/new" element={<CreateEvent />} />
            <Route path="/events/:id" element={<EventDetail />} />
            <Route path="/events" element={<Dashboard />} />
            <Route path="/people" element={<People />} />
            <Route path="/people/:id" element={<PersonProfile />} />
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
