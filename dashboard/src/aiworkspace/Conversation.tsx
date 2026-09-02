/**
 * The conversation column — messages and the composer.
 *
 * Conversation dominates the screen, which is the point of the layout: this is a
 * working surface, not a dashboard card. Messages are dense and left-aligned
 * with a small role gutter rather than opposed chat bubbles — the transcript is
 * read as a document, and alternating bubbles waste half the width.
 *
 * A failed turn renders as a failure with a retry affordance, never as an empty
 * assistant message. Hiding it would make the transcript claim a turn happened
 * that did not.
 */

import { useEffect, useRef, useState } from "react";
import type { Conversation as ConversationType, Message } from "./types";

function timeOf(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ""
    : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function RoleTag({ message }: { message: Message }) {
  if (message.role === "user") {
    return <span className="text-[11px] font-medium text-ink-muted">You</span>;
  }
  if (message.role === "event") {
    return <span className="text-[11px] font-medium text-accent-violet">MondayOS</span>;
  }
  return (
    <span className="flex items-baseline gap-1.5">
      <span className="text-[11px] font-medium text-brand-400">Monday</span>
      {message.model && (
        <span className="font-mono text-[9px] text-ink-faint" title={`provider: ${message.provider}`}>
          {message.model}
        </span>
      )}
    </span>
  );
}

function MessageRow({
  message,
  isLast,
  onRetry,
  onSave,
  busy,
}: {
  message: Message;
  isLast: boolean;
  onRetry: () => void;
  onSave: (id: string) => void;
  busy: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const [saved, setSaved] = useState(false);

  const copy = () => {
    void navigator.clipboard?.writeText(message.content).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  if (message.role === "event") {
    return (
      <div className="px-6 py-1.5">
        <div className="flex items-center gap-2 text-[11px] text-accent-violet/80">
          <span className="h-px flex-1 bg-line" />
          <span>{message.content}</span>
          <span className="h-px flex-1 bg-line" />
        </div>
      </div>
    );
  }

  const failed = Boolean(message.error);

  return (
    <div className="group px-6 py-3">
      <div className="mb-1 flex items-baseline justify-between gap-3">
        <RoleTag message={message} />
        <div className="flex items-center gap-2">
          {!failed && message.role === "assistant" && (
            <>
              <button
                onClick={copy}
                className="text-[10px] text-ink-faint opacity-0 transition group-hover:opacity-100 hover:text-ink"
              >
                {copied ? "copied" : "copy"}
              </button>
              <button
                onClick={() => {
                  onSave(message.id);
                  setSaved(true);
                }}
                disabled={saved}
                className="text-[10px] text-ink-faint opacity-0 transition group-hover:opacity-100 hover:text-brand-400 disabled:opacity-100 disabled:text-ink-faint/60"
                title="Save this answer to project knowledge"
              >
                {saved ? "saved" : "save to knowledge"}
              </button>
            </>
          )}
          <span className="text-[10px] text-ink-faint/70">{timeOf(message.created_at)}</span>
        </div>
      </div>

      {failed ? (
        <div className="rounded-lg border border-status-blocked/30 bg-status-blocked/5 px-3 py-2">
          <div className="text-[12px] text-status-blocked">Response failed</div>
          <div className="mt-0.5 text-[11px] leading-relaxed text-ink-muted">{message.error}</div>
          {isLast && (
            <button
              onClick={onRetry}
              disabled={busy}
              className="focus-ring mt-2 rounded border border-line px-2 py-1 text-[11px] text-ink-muted transition hover:border-brand-400/50 hover:text-ink disabled:opacity-50"
            >
              {busy ? "retrying…" : "Retry"}
            </button>
          )}
        </div>
      ) : (
        <div className="whitespace-pre-wrap text-[13px] leading-relaxed text-ink">
          {message.content}
        </div>
      )}
    </div>
  );
}

export function ConversationView({
  conversation,
  sending,
  loading,
  onSend,
  onRetry,
  onSave,
  onRename,
}: {
  conversation: ConversationType | null;
  sending: boolean;
  loading: boolean;
  onSend: (text: string) => void;
  onRetry: () => void;
  onSave: (messageId: string) => void;
  onRename: (id: string, title: string) => void;
}) {
  const [draft, setDraft] = useState("");
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [conversation?.messages.length, sending]);

  if (!conversation) {
    return (
      <div className="flex h-full flex-1 items-center justify-center">
        <div className="max-w-sm text-center">
          <div className="text-[13px] text-ink-muted">No conversation selected</div>
          <div className="mt-1 text-[11px] text-ink-faint">
            Pick one from the left, or start a new one. MondayOS loads the project's
            architecture, tasks and git state before it answers.
          </div>
        </div>
      </div>
    );
  }

  const submit = () => {
    const text = draft.trim();
    if (!text || sending) return;
    setDraft("");
    onSend(text);
  };

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col">
      <header className="flex shrink-0 items-center justify-between border-b border-line px-6 py-2.5">
        {editingTitle ? (
          <input
            autoFocus
            value={titleDraft}
            onChange={(e) => setTitleDraft(e.target.value)}
            onBlur={() => {
              setEditingTitle(false);
              if (titleDraft.trim()) onRename(conversation.id, titleDraft);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") e.currentTarget.blur();
              if (e.key === "Escape") setEditingTitle(false);
            }}
            className="focus-ring w-full rounded border border-line bg-canvas px-2 py-0.5 text-[13px] text-ink"
          />
        ) : (
          <button
            onClick={() => {
              setTitleDraft(conversation.title);
              setEditingTitle(true);
            }}
            className="truncate text-[13px] font-medium text-ink transition hover:text-brand-400"
            title="Rename"
          >
            {conversation.title}
          </button>
        )}
        <span className="ml-3 shrink-0 font-mono text-[10px] text-ink-faint">
          {conversation.id}
        </span>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {loading ? (
          <div className="px-6 py-4 text-[12px] text-ink-faint">Loading…</div>
        ) : conversation.messages.length === 0 ? (
          <div className="px-6 py-6 text-[12px] text-ink-faint">
            Ask anything about this project.
          </div>
        ) : (
          <div className="divide-y divide-line/40">
            {conversation.messages.map((message, i) => (
              <MessageRow
                key={message.id}
                message={message}
                isLast={i === conversation.messages.length - 1}
                onRetry={onRetry}
                onSave={onSave}
                busy={sending}
              />
            ))}
          </div>
        )}
        {sending && (
          <div className="px-6 py-3 text-[12px] text-ink-faint">
            <span className="animate-pulse-soft">Monday is thinking…</span>
          </div>
        )}
        <div ref={endRef} />
      </div>

      <div className="shrink-0 border-t border-line px-6 py-3">
        <div className="flex items-end gap-2">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            rows={1}
            placeholder="Ask MondayOS about this project…"
            className="focus-ring max-h-40 min-h-[38px] flex-1 resize-y rounded-lg border border-line bg-canvas px-3 py-2 text-[13px] text-ink placeholder:text-ink-faint"
          />
          <button
            onClick={submit}
            disabled={sending || !draft.trim()}
            className="focus-ring h-[38px] shrink-0 rounded-lg border border-brand-400/40 bg-brand-500/10 px-4 text-[12px] font-medium text-brand-200 transition hover:bg-brand-500/20 disabled:opacity-40"
          >
            Send
          </button>
        </div>
        <div className="mt-1.5 text-[10px] text-ink-faint/70">
          Enter to send · Shift+Enter for a new line
        </div>
      </div>
    </div>
  );
}
