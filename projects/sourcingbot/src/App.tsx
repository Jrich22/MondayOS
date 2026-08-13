import type { FC } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "@/components/shell/AppShell";
import Workspace from "@/pages/Workspace";
import ReqDetail from "@/pages/ReqDetail";
import ReqAuthoring from "@/pages/ReqAuthoring";
import SourcingSession from "@/pages/SourcingSession";
import Candidates from "@/pages/Candidates";
import CandidateProfile from "@/pages/CandidateProfile";

/**
 * Routes for the foundation increment. No LinkedIn workflow route exists yet —
 * that surface lands in a later increment behind the supervision gate in
 * lib/linkedin.ts (docs/ROADMAP.md).
 */
const App: FC = () => (
  <Routes>
    <Route element={<AppShell />}>
      <Route path="/" element={<Workspace />} />
      <Route path="/reqs/:reqId/edit" element={<ReqAuthoring />} />
      <Route path="/reqs/:reqId/session" element={<SourcingSession />} />
      <Route path="/reqs/:reqId" element={<ReqDetail />} />
      <Route path="/candidates" element={<Candidates />} />
      <Route path="/candidates/:candidateId" element={<CandidateProfile />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Route>
  </Routes>
);

export default App;
