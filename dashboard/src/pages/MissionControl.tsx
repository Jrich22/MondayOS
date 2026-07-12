import { useMemo, useState } from "react";
import { MondayBrain, STATE_LABELS, type BrainState } from "@/components/monday";
import {
  OS_STATE,
  taskCounts,
  deriveBrainState,
  type AgentActivity,
  type ProductStatus,
  type TaskStatus,
} from "@/lib/os-data";
import { CommandInterface } from "@/components/mission/CommandInterface";

/**
 * MondayOS Mission Control — the operating-system dashboard.
 *
 * The centre is Monday's Brain, whose state is *derived from the OS itself*
 * (agents, tasks, activity) rather than hand-set, so the chamber visibly
 * reflects what MondayOS is doing. Around it: system vitals, the agent fleet,
 * the task queue, the products MondayOS manages (Cue is one row here — this
 * dashboard is the OS, not any single product), and a live activity stream.
 *
 * An override lets an operator preview any state; leaving it on "Auto (live)"
 * tracks the derived state.
 */

const ACTIVITY_DOT: Record<AgentActivity, string> = {
  idle: "bg-status-idle",
  thinking: "bg-status-thinking",
  executing: "bg-status-executing",
  awaiting: "bg-status-awaiting",
  blocked: "bg-status-blocked",
  learning: "bg-status-learning",
};

const TASK_BADGE: Record<TaskStatus, string> = {
  active: "text-status-executing border-status-executing/40 bg-status-executing/10",
  blocked: "text-status-blocked border-status-blocked/40 bg-status-blocked/10",
  review: "text-status-awaiting border-status-awaiting/40 bg-status-awaiting/10",
  completed: "text-status-completed border-status-completed/40 bg-status-completed/10",
};

const PRODUCT_DOT: Record<ProductStatus, string> = {
  operational: "bg-status-completed",
  building: "bg-status-executing",
  attention: "bg-status-awaiting",
};

const STATES: BrainState[] = [
  "idle",
  "thinking",
  "executing",
  "awaiting",
  "blocked",
  "completed",
  "learning",
];

function fmtUptime(s: number): string {
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return `${h}h ${m}m`;
}

export function MissionControl() {
  const os = OS_STATE;
  const derived = useMemo(() => deriveBrainState(os), [os]);
  const [override, setOverride] = useState<BrainState | "auto">("auto");
  const [cmdOpen, setCmdOpen] = useState(false);

  const state: BrainState = override === "auto" ? derived : override;
  const counts = taskCounts(os);

  return (
    <div className="min-h-screen">
      {/* Top bar. */}
      <header className="sticky top-0 z-20 flex items-center justify-between border-b border-line bg-canvas/70 px-6 py-3 backdrop-blur-md">
        <div className="flex items-center gap-3">
          <div className="grid h-8 w-8 place-items-center rounded-lg bg-gradient-to-br from-accent-cyan to-accent-violet text-sm font-bold text-canvas">
            M
          </div>
          <div>
            <div className="text-sm font-semibold tracking-tight">MondayOS</div>
            <div className="text-[11px] text-ink-faint">Mission Control</div>
          </div>
        </div>
        <div className="flex items-center gap-4 text-xs">
          <span className="flex items-center gap-1.5 text-ink-muted">
            <span
              className={`h-2 w-2 rounded-full ${os.system.healthy ? "bg-status-completed" : "bg-status-blocked"} animate-pulse-soft`}
            />
            {os.system.healthy ? "Healthy" : "Degraded"}
          </span>
          <span className="text-ink-faint">v{os.system.version}</span>
          <span className="text-ink-faint">up {fmtUptime(os.system.uptimeSeconds)}</span>
          <button
            onClick={() => setCmdOpen(true)}
            className="focus-ring rounded-lg border border-line bg-canvas-raised px-3 py-1.5 text-ink-muted transition hover:border-brand-400/50 hover:text-ink"
          >
            Ask Monday <kbd className="ml-1 text-ink-faint">⌘K</kbd>
          </button>
        </div>
      </header>

      {/* Body grid. */}
      <div className="mx-auto grid max-w-[1600px] grid-cols-1 gap-4 p-4 lg:grid-cols-12">
        {/* Left rail — vitals + fleet. */}
        <div className="flex flex-col gap-4 lg:col-span-3">
          <section className="card p-4">
            <h2 className="mb-3 text-xs font-semibold uppercase tracking-wider text-ink-faint">
              System
            </h2>
            <dl className="grid grid-cols-2 gap-3 text-sm">
              <Vital label="Session" value={os.system.sessionId} />
              <Vital label="Uptime" value={fmtUptime(os.system.uptimeSeconds)} />
              <Vital label="Agents" value={`${os.agents.length}`} />
              <Vital
                label="Active tasks"
                value={`${counts.active}`}
                tone={counts.blocked > 0 ? undefined : "good"}
              />
            </dl>
          </section>

          <section className="card min-h-0 flex-1 p-4">
            <h2 className="mb-3 text-xs font-semibold uppercase tracking-wider text-ink-faint">
              Agent fleet
            </h2>
            <ul className="space-y-2.5">
              {os.agents.map((a) => (
                <li key={a.id} className="flex items-center gap-3">
                  <span className={`h-2 w-2 shrink-0 rounded-full ${ACTIVITY_DOT[a.activity]}`} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="truncate text-sm text-ink">{a.name}</span>
                      <span className="shrink-0 text-[11px] capitalize text-ink-faint">
                        {a.activity}
                      </span>
                    </div>
                    <div className="truncate text-[11px] text-ink-faint">
                      {a.task ?? a.role}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          </section>
        </div>

        {/* Centre — Monday's Brain. */}
        <div className="lg:col-span-6">
          <section className="card relative flex h-full min-h-[560px] flex-col overflow-hidden">
            <div className="pointer-events-none absolute left-5 top-4 z-10">
              <div className="text-xs uppercase tracking-wider text-ink-faint">Monday's Brain</div>
              <div className="mt-0.5 flex items-center gap-2">
                <span className={`h-2 w-2 rounded-full ${ACTIVITY_DOT[state as AgentActivity] ?? "bg-status-completed"}`} />
                <span className="text-lg font-semibold tracking-tight">{STATE_LABELS[state]}</span>
              </div>
              <div className="mt-0.5 text-[11px] text-ink-faint">
                {override === "auto" ? "Live — reflecting OS activity" : "Manual preview"}
              </div>
            </div>

            <MondayBrain
              state={state}
              onActivate={() => setCmdOpen(true)}
              className="absolute inset-0 h-full w-full"
            />

            {/* State override / preview. */}
            <div className="pointer-events-auto absolute inset-x-0 bottom-0 z-10 flex flex-wrap justify-center gap-1.5 p-3">
              <StateChip active={override === "auto"} onClick={() => setOverride("auto")}>
                Auto (live)
              </StateChip>
              {STATES.map((s) => (
                <StateChip key={s} active={override === s} onClick={() => setOverride(s)}>
                  {STATE_LABELS[s]}
                </StateChip>
              ))}
            </div>
          </section>
        </div>

        {/* Right rail — tasks + products. */}
        <div className="flex flex-col gap-4 lg:col-span-3">
          <section className="card p-4">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-xs font-semibold uppercase tracking-wider text-ink-faint">
                Task queue
              </h2>
              <div className="flex gap-1.5 text-[10px]">
                <Count n={counts.active} label="active" cls="text-status-executing" />
                <Count n={counts.blocked} label="blocked" cls="text-status-blocked" />
                <Count n={counts.review} label="review" cls="text-status-awaiting" />
              </div>
            </div>
            <ul className="space-y-2">
              {os.tasks.slice(0, 6).map((t) => (
                <li key={t.id} className="flex items-center gap-2">
                  <span
                    className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] font-medium capitalize ${TASK_BADGE[t.status]}`}
                  >
                    {t.status}
                  </span>
                  <span className="truncate text-sm text-ink-muted">{t.title}</span>
                </li>
              ))}
            </ul>
          </section>

          <section className="card min-h-0 flex-1 p-4">
            <h2 className="mb-3 text-xs font-semibold uppercase tracking-wider text-ink-faint">
              Managed products
            </h2>
            <ul className="space-y-3">
              {os.products.map((p) => (
                <li key={p.key} className="rounded-xl border border-line bg-canvas-overlay/50 p-3">
                  <div className="flex items-center justify-between">
                    <span className="flex items-center gap-2 text-sm font-medium text-ink">
                      <span className={`h-2 w-2 rounded-full ${PRODUCT_DOT[p.status]}`} />
                      {p.name}
                    </span>
                    <span className="text-[11px] text-ink-faint">{p.openTasks} open</span>
                  </div>
                  <div className="mt-1 text-[11px] text-ink-faint">{p.summary}</div>
                </li>
              ))}
            </ul>
          </section>
        </div>

        {/* Bottom — activity stream + knowledge. */}
        <div className="grid grid-cols-1 gap-4 lg:col-span-12 lg:grid-cols-4">
          <section className="card p-4 lg:col-span-3">
            <h2 className="mb-3 text-xs font-semibold uppercase tracking-wider text-ink-faint">
              Activity
            </h2>
            <ul className="space-y-2">
              {os.activity.map((e) => (
                <li key={e.id} className="flex items-center gap-3 text-sm">
                  <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${ACTIVITY_DOT[e.kind as AgentActivity] ?? "bg-status-completed"}`} />
                  <span className="w-16 shrink-0 text-[11px] text-ink-faint">{e.at}</span>
                  <span className="w-24 shrink-0 truncate text-ink-muted">{e.agent}</span>
                  <span className="truncate text-ink-muted">{e.message}</span>
                </li>
              ))}
            </ul>
          </section>
          <section className="card p-4">
            <h2 className="mb-3 text-xs font-semibold uppercase tracking-wider text-ink-faint">
              Knowledge
            </h2>
            <dl className="grid grid-cols-2 gap-3 text-sm">
              <Vital label="Documents" value={`${os.knowledge.documents}`} />
              <Vital label="Decisions" value={`${os.knowledge.decisions}`} />
              <Vital label="Sprints" value={`${os.knowledge.sprints}`} />
              <Vital label="Indexed today" value={`${os.knowledge.indexedToday}`} tone="good" />
            </dl>
          </section>
        </div>
      </div>

      <CommandInterface open={cmdOpen} onClose={() => setCmdOpen(false)} />
    </div>
  );
}

function Vital({ label, value, tone }: { label: string; value: string; tone?: "good" }) {
  return (
    <div>
      <dt className="text-[11px] text-ink-faint">{label}</dt>
      <dd className={`text-base font-semibold ${tone === "good" ? "text-status-completed" : "text-ink"}`}>
        {value}
      </dd>
    </div>
  );
}

function Count({ n, label, cls }: { n: number; label: string; cls: string }) {
  return (
    <span className={`${cls}`} title={label}>
      {n} <span className="text-ink-faint">{label}</span>
    </span>
  );
}

function StateChip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`focus-ring rounded-full border px-3 py-1 text-[11px] transition ${
        active
          ? "border-brand-400 bg-brand-400/20 text-ink"
          : "border-line bg-canvas-raised/60 text-ink-muted hover:border-line-strong hover:text-ink"
      }`}
    >
      {children}
    </button>
  );
}
