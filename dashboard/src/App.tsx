import { AppProvider } from "@/state/store";
import { MissionControl } from "@/pages/MissionControl";
import { ErrorBoundary } from "@/common/ErrorBoundary";

/**
 * MondayOS Mission Control. A single interactive surface: Monday's Brain as the
 * command + navigation center, with workspaces opening beside it. The AppProvider
 * owns client state and the adapter boundary to MondayOS (live or demo); the
 * top-level ErrorBoundary keeps a failure from blanking the whole OS.
 */
export default function App() {
  return (
    <ErrorBoundary label="MondayOS">
      <AppProvider>
        <MissionControl />
      </AppProvider>
    </ErrorBoundary>
  );
}
