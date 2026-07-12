import type { Turn } from "@/state/store";
import type { CommandOutcome, DataView, ResultAction } from "@/command/execute";
import type { ParsedCommand } from "@/command/intents";

/**
 * Renders one conversation turn. Monday's answers are structured and
 * actionable: a spoken line, an optional typed data view, and action buttons
 * (follow-up commands, navigation, or a write confirmation). Purely
 * presentational — all behavior is delegated up via the handler props.
 */

interface Props {
  turn: Turn;
  dismissed: boolean;
  onAction: (action: ResultAction) => void;
  onConfirm: (parsed: ParsedCommand) => void;
  onCancel: (turnId: string) => void;
}

export function ResultCard({ turn, dismissed, onAction, onConfirm, onCancel }: Props) {
  if (turn.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-2xl rounded-br-sm bg-brand-500/20 px-3.5 py-2 text-sm text-ink">
          {turn.text}
        </div>
      </div>
    );
  }

  const outcome = turn.outcome;
  return (
    <div className="flex gap-2.5">
      <div className="mt-1 h-2 w-2 shrink-0 rounded-full bg-gradient-to-br from-accent-cyan to-accent-violet" />
      <div className="min-w-0 flex-1">
        <div
          className={`text-sm ${outcome?.kind === "blocked" ? "text-status-blocked" : "text-ink"}`}
        >
          {turn.text}
        </div>

        {outcome?.kind === "answer" && <DataBlock data={outcome.data} />}

        {outcome && !dismissed && <Actions outcome={outcome} onAction={onAction} onConfirm={onConfirm} onCancel={() => onCancel(turn.id)} />}
        {dismissed && <div className="mt-1 text-[11px] text-ink-faint">— dismissed</div>}
      </div>
    </div>
  );
}

function Actions({
  outcome,
  onAction,
  onConfirm,
  onCancel,
}: {
  outcome: CommandOutcome;
  onAction: (a: ResultAction) => void;
  onConfirm: (p: ParsedCommand) => void;
  onCancel: () => void;
}) {
  if (outcome.kind === "blocked") {
    return (
      <div className="mt-2 inline-flex items-center gap-2 rounded-lg border border-status-blocked/30 bg-status-blocked/10 px-2.5 py-1 text-[11px] text-status-blocked">
        Gated — requires MondayOS approval policy
      </div>
    );
  }
  const actions = outcome.kind === "answer" || outcome.kind === "confirm" ? outcome.actions : [];
  if (!actions.length) return null;
  return (
    <div className="mt-2.5 flex flex-wrap gap-2">
      {actions.map((a, i) => (
        <button
          key={i}
          onClick={() => {
            if (a.confirm && outcome.kind === "confirm") onConfirm(outcome.parsed);
            else if (a.label.toLowerCase() === "cancel") onCancel();
            else onAction(a);
          }}
          className={`focus-ring rounded-lg border px-2.5 py-1 text-[12px] transition ${
            a.variant === "primary"
              ? "border-brand-400 bg-brand-400/20 text-ink hover:bg-brand-400/30"
              : a.variant === "danger"
                ? "border-status-blocked/40 text-status-blocked hover:bg-status-blocked/10"
                : "border-line text-ink-muted hover:border-line-strong hover:text-ink"
          }`}
        >
          {a.label}
        </button>
      ))}
    </div>
  );
}

function DataBlock({ data }: { data: DataView }) {
  if (data.type === "none") return null;
  const wrap = (children: React.ReactNode) => (
    <ul className="mt-2 space-y-1 rounded-lg border border-line bg-canvas-overlay/40 p-2 text-[12px] text-ink-muted">
      {children}
    </ul>
  );

  switch (data.type) {
    case "status":
      return wrap(
        <li>
          {data.status.provider}/{data.status.model} · session {data.status.sessionId}
        </li>,
      );
    case "products":
      return wrap(data.products.map((p) => <li key={p.key}>● {p.name} — {p.openTasks} open</li>));
    case "product":
      return wrap(
        <>
          <li>● {data.product.name} — {data.product.summary}</li>
          {data.product.sprint && <li>{data.product.sprint.name}: {data.product.sprint.done}/{data.product.sprint.total}</li>}
        </>,
      );
    case "tasks":
      return wrap(
        data.tasks.slice(0, 6).map((t) => (
          <li key={t.id}>
            {t.id} · {t.title} <span className="text-ink-faint">({t.status})</span>
          </li>
        )),
      );
    case "agents":
      return wrap(data.agents.slice(0, 8).map((a) => <li key={a.id}>{a.name} — {a.activity}</li>));
    case "approvals":
      return wrap(data.approvals.map((a) => <li key={a.id}>{a.id} · {a.summary}</li>));
    case "activity":
      return wrap(data.events.slice(0, 4).map((e) => <li key={e.id}>{e.agent}: {e.message}</li>));
    case "prs":
      return wrap(data.prs.map((p) => <li key={p.number}>#{p.number} {p.title} ({p.state})</li>));
    case "knowledge":
      return wrap(data.items.map((k) => <li key={k.id}>{k.id} · {k.title} ({k.kind})</li>));
    case "runs":
      return wrap(data.runs.map((r) => <li key={r.id}>{r.id} on {r.taskId} — {r.status}</li>));
    default:
      return null;
  }
}
