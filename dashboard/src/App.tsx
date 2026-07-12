import { MissionControl } from "@/pages/MissionControl";

/**
 * MondayOS Mission Control is a single-surface dashboard for now — the OS's
 * operating view. No router yet: the dashboard is the app. As the OS grows
 * (agent detail, knowledge browser, product drill-downs) this becomes the shell
 * around those routes.
 */
export default function App() {
  return <MissionControl />;
}
