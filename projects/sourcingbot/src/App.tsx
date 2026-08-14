import type { FC } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "@/components/shell/AppShell";
import Workspace from "@/pages/Workspace";
import ReqDetail from "@/pages/ReqDetail";
import ReqAuthoring from "@/pages/ReqAuthoring";
import SourcingSession from "@/pages/SourcingSession";
import Dashboard from "@/pages/Dashboard";
import CandidateWorkspace from "@/pages/CandidateWorkspace";
import CandidateProfile from "@/pages/CandidateProfile";

/**
 * Routes for the foundation increment. No LinkedIn workflow route exists yet —
 * that surface lands in a later increment behind the supervision gate in
 * lib/sourcing-session.ts (docs/ROADMAP.md).
 */
const App: FC = () => (
  <Routes>
    <Route element={<AppShell />}>
      {/* The Dashboard is home: a recruiter lands on what to do next, not on a
          list of records. The Candidate Workspace remains available in full at
          /talent. See ADR-014 and ADR-015. */}
      <Route path="/" element={<Dashboard />} />
      <Route path="/talent" element={<CandidateWorkspace />} />
      <Route path="/reqs" element={<Workspace />} />
      <Route path="/reqs/:reqId/edit" element={<ReqAuthoring />} />
      <Route path="/reqs/:reqId/session" element={<SourcingSession />} />
      <Route path="/reqs/:reqId" element={<ReqDetail />} />
      <Route path="/candidates/:candidateId" element={<CandidateProfile />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Route>
  </Routes>
);

export default App;
