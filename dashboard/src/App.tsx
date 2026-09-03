import { useCallback, useState } from "react";
import { AppProvider } from "@/state/store";
import { AIWorkspace } from "@/aiworkspace/AIWorkspace";
import { MissionControl } from "@/pages/MissionControl";
import { CommandPalette } from "@/components/command/CommandPalette";
import { ErrorBoundary } from "@/common/ErrorBoundary";

/**
 * MondayOS.
 *
 * The application **is** the conversation with Monday. That is the whole
 * architecture of this file: `AIWorkspace` owns the window, and Mission Control
 * is an overlay you summon when you want diagnostics.
 *
 * It used to be the other way round — Mission Control was the shell and the
 * workspace rendered inside one of its sections. That made the conversation a
 * feature of a dashboard, and every design decision downstream inherited the
 * mistake: chrome above the conversation, a project browser beside it, a
 * dashboard's information density around it.
 *
 * Inverting it is the point. You launch MondayOS and you are already talking to
 * it. Everything else is somewhere you can go.
 */
export default function App() {
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
  // Stable callbacks: the workspace holds the conversation, the loaded context
  // and the scroll position, and re-rendering that tree every time the overlay
  // toggles costs work for no reason.
  const openDiagnostics = useCallback(() => setDiagnosticsOpen(true), []);
  const closeDiagnostics = useCallback(() => setDiagnosticsOpen(false), []);

  return (
    <ErrorBoundary label="MondayOS">
      <AppProvider>
        <div className="h-screen overflow-hidden bg-canvas">
          <ErrorBoundary label="AI Workspace">
            <AIWorkspace onOpenDiagnostics={openDiagnostics} />
          </ErrorBoundary>

          {/* Mission Control, as an overlay rather than a shell. Reaching it is
              deliberate; leaving it returns you to the conversation, which is
              where work happens. */}
          {diagnosticsOpen && (
            <div className="fixed inset-0 z-40 bg-canvas">
              <ErrorBoundary label="Mission Control">
                <MissionControl onClose={closeDiagnostics} />
              </ErrorBoundary>
            </div>
          )}

          {/* The command palette stays global: ⌘K should work wherever you are. */}
          <CommandPalette />
        </div>
      </AppProvider>
    </ErrorBoundary>
  );
}
