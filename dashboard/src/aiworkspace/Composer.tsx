/**
 * The composer — where every interaction starts.
 *
 * Three things make it feel like a tool rather than a text box: it always states
 * which project context is loaded, it offers commands without getting in the
 * way, and Stop is a real button that does a real thing rather than a spinner
 * you wait out.
 *
 * The slash menu opens on `/` at the start of an empty line and filters as you
 * type. It closes on Escape, on a space, and on any non-matching input — a
 * command palette that has to be dismissed is a palette that gets in the way.
 */

import { useEffect, useMemo, useRef, useState } from "react";

export interface SlashCommand {
  name: string;
  hint: string;
}

/**
 * The command vocabulary. Every one maps to an existing MondayOS capability —
 * nothing here is a placeholder for something that does not work yet.
 */
export const COMMANDS: SlashCommand[] = [
  { name: "continue", hint: "Reopen the last project and conversation" },
  { name: "status", hint: "Project, branch, active task, context" },
  { name: "context", hint: "What Monday currently has loaded" },
  { name: "tasks", hint: "Open tasks for this project" },
  { name: "knowledge", hint: "Search this project's knowledge" },
  { name: "learn", hint: "Save the last response to project knowledge" },
  { name: "new", hint: "Start a new conversation" },
  { name: "switch", hint: "Switch project — /switch growth-bot" },
  { name: "help", hint: "List commands" },
];

export function Composer({
  onSend,
  onCommand,
  onStop,
  sending,
  project,
  contextLoaded,
  provider,
  disabled,
}: {
  onSend: (text: string) => void;
  onCommand: (name: string, args: string) => void;
  onStop: () => void;
  sending: boolean;
  project: string;
  contextLoaded: boolean;
  provider: string;
  disabled: boolean;
}) {
  const [draft, setDraft] = useState("");
  const [menuIndex, setMenuIndex] = useState(0);
  const [history, setHistory] = useState<string[]>([]);
  const [historyIndex, setHistoryIndex] = useState(-1);
  const areaRef = useRef<HTMLTextAreaElement>(null);

  const slashQuery = useMemo(() => {
    const match = /^\/([a-z]*)$/.exec(draft.trim());
    return draft.startsWith("/") && match ? match[1] : null;
  }, [draft]);

  const matches = useMemo(
    () => (slashQuery === null ? [] : COMMANDS.filter((c) => c.name.startsWith(slashQuery))),
    [slashQuery],
  );
  const menuOpen = matches.length > 0;

  useEffect(() => setMenuIndex(0), [slashQuery]);

  // Grow with content up to a cap, so a long prompt is visible without the
  // composer eating the conversation.
  useEffect(() => {
    const el = areaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [draft]);

  const submit = (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || sending) return;
    setHistory((h) => [trimmed, ...h].slice(0, 50));
    setHistoryIndex(-1);
    setDraft("");
    if (trimmed.startsWith("/")) {
      const [name, ...rest] = trimmed.slice(1).split(/\s+/);
      onCommand(name.toLowerCase(), rest.join(" "));
      return;
    }
    onSend(trimmed);
  };

  const pick = (name: string) => {
    setDraft(`/${name} `);
    areaRef.current?.focus();
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (menuOpen) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setMenuIndex((i) => (i + 1) % matches.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setMenuIndex((i) => (i - 1 + matches.length) % matches.length);
        return;
      }
      if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) {
        e.preventDefault();
        pick(matches[menuIndex].name);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setDraft("");
        return;
      }
    }

    if (e.key === "Escape" && sending) {
      e.preventDefault();
      onStop();
      return;
    }

    // Up arrow from an empty composer recalls the previous input — only when
    // empty, so it never fights with cursor movement in a real draft.
    if (e.key === "ArrowUp" && !draft && history.length) {
      e.preventDefault();
      const next = Math.min(historyIndex + 1, history.length - 1);
      setHistoryIndex(next);
      setDraft(history[next]);
      return;
    }
    if (e.key === "ArrowDown" && historyIndex >= 0) {
      e.preventDefault();
      const next = historyIndex - 1;
      setHistoryIndex(next);
      setDraft(next < 0 ? "" : history[next]);
      return;
    }

    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit(draft);
    }
  };

  return (
    <div className="shrink-0 border-t border-line bg-canvas/60 px-6 py-3 backdrop-blur">
      <div className="relative">
        {menuOpen && (
          <div className="absolute bottom-full left-0 z-20 mb-2 w-[360px] overflow-hidden rounded-lg border border-line bg-canvas-overlay shadow-card">
            {matches.map((cmd, i) => (
              <button
                key={cmd.name}
                onMouseDown={(e) => {
                  e.preventDefault();
                  pick(cmd.name);
                }}
                onMouseEnter={() => setMenuIndex(i)}
                className={`flex w-full items-baseline gap-3 px-3 py-1.5 text-left transition ${
                  i === menuIndex ? "bg-brand-500/10" : ""
                }`}
              >
                <span className="font-mono text-[12px] text-brand-200">/{cmd.name}</span>
                <span className="truncate text-[11px] text-ink-faint">{cmd.hint}</span>
              </button>
            ))}
          </div>
        )}

        <div className="flex items-end gap-2">
          <button
            type="button"
            disabled
            title="Attachments arrive with the artifact system"
            className="mb-[3px] h-[34px] w-[34px] shrink-0 rounded-lg border border-line text-[15px] text-ink-faint/50"
          >
            +
          </button>

          <textarea
            ref={areaRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            rows={1}
            disabled={disabled}
            placeholder={disabled ? "Select a conversation to begin" : "Ask Monday anything…"}
            className="focus-ring max-h-[200px] min-h-[40px] flex-1 resize-none rounded-lg border border-line bg-canvas px-3 py-2 text-[13px] leading-relaxed text-ink placeholder:text-ink-faint disabled:opacity-50"
          />

          {sending ? (
            <button
              onClick={onStop}
              className="focus-ring mb-[3px] h-[34px] shrink-0 rounded-lg border border-status-blocked/40 bg-status-blocked/10 px-3 text-[12px] font-medium text-status-blocked transition hover:bg-status-blocked/20"
            >
              Stop
            </button>
          ) : (
            <button
              onClick={() => submit(draft)}
              disabled={!draft.trim() || disabled}
              className="focus-ring mb-[3px] h-[34px] shrink-0 rounded-lg border border-brand-400/40 bg-brand-500/10 px-4 text-[12px] font-medium text-brand-200 transition hover:bg-brand-500/20 disabled:opacity-40"
            >
              Send
            </button>
          )}
        </div>
      </div>

      {/* Always states what context is loaded. The operator should never have to
          guess which project a question is about to be answered against. */}
      <div className="mt-2 flex items-center gap-2 text-[10px] text-ink-faint">
        <span className="text-ink-faint/70">MondayOS</span>
        <span className="text-ink-faint/40">·</span>
        <span className={contextLoaded ? "text-status-completed/80" : "text-status-awaiting"}>
          {contextLoaded ? "Context loaded" : "No context"}
        </span>
        <span className="text-ink-faint/40">·</span>
        <span className="text-ink-muted">{project || "no project"}</span>
        {provider && (
          <>
            <span className="text-ink-faint/40">·</span>
            <span className="font-mono">{provider}</span>
          </>
        )}
        <span className="ml-auto text-ink-faint/50">
          {sending ? "Esc to stop" : "/ for commands · ⇧⏎ newline"}
        </span>
      </div>
    </div>
  );
}
