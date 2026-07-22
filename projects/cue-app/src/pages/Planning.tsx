import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import type { CueEvent } from "@/lib/types";
import { useEvent } from "@/lib/store";
import { useGuests } from "@/lib/guests";
import { usePlan, savePlan, planId } from "@/lib/planning-store";
import {
  readiness,
  capacityDemand,
  currentMember,
  localDate,
  type EventPlan,
  type ReadinessState,
  type MilestoneStatus,
  type RiskSeverity,
} from "@/lib/planning";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/cn";

/**
 * Event Planning workspace (slice 1). Reads the CANONICAL event and guest state
 * (never a copy) and the planning collection, and lets an operator structure the
 * event, assign ownership, manage milestones/risks, and read explainable
 * operational readiness with capacity-vs-demand warnings. Deferred scope
 * (templates, venues, registration, sessions, weighted %) is intentionally absent.
 *
 * The outer route component resolves the event first and renders the inner
 * `PlanningWorkspace` only for a valid event, so hooks that read the plan never
 * receive an invalid/stale event (finding #1) — and hook order stays stable.
 */
export default function Planning() {
  const { id } = useParams<{ id: string }>();
  const event = useEvent(id);

  if (!event) {
    return (
      <div className="mx-auto max-w-3xl p-8">
        <h1 className="text-lg font-semibold text-ink">Event not found</h1>
        <p className="mt-1 text-sm text-ink-muted">
          This planning link points to an event that no longer exists.
        </p>
        <Link to="/events" className="mt-4 inline-block text-brand-400 hover:underline">
          ← Back to events
        </Link>
      </div>
    );
  }
  return <PlanningWorkspace event={event} />;
}

const STATE_LABEL: Record<ReadinessState, string> = {
  complete: "Complete",
  attention: "Attention Needed",
  blocked: "Blocked",
  "not-applicable": "Not Applicable",
};

const STATE_STYLE: Record<ReadinessState, string> = {
  complete: "border-emerald-500/40 text-emerald-300 bg-emerald-500/10",
  attention: "border-amber-500/40 text-amber-300 bg-amber-500/10",
  blocked: "border-rose-500/40 text-rose-300 bg-rose-500/10",
  "not-applicable": "border-line text-ink-muted bg-white/5",
};

function PlanningWorkspace({ event }: { event: CueEvent }) {
  const guests = useGuests(event.id);
  const plan = usePlan(event);
  const now = Date.now();

  const report = useMemo(() => readiness(event, guests, plan, now), [event, guests, plan, now]);
  const demand = useMemo(() => capacityDemand(event, guests), [event, guests]);

  const update = (next: Partial<EventPlan>) => savePlan({ ...plan, ...next });
  const me = currentMember();
  const ownerName = (ownerId?: string | null) =>
    ownerId ? plan.members.find((m) => m.id === ownerId)?.name ?? me.name : "Unassigned";

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6 sm:p-8">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm text-ink-muted">
            <Link to={`/events/${event.id}`} className="focus-ring max-w-[16rem] truncate rounded hover:text-ink">
              ← {event.title}
            </Link>
            <span aria-hidden>·</span>
            <span>Planning workspace</span>
          </div>
          <h1 className="mt-1 text-2xl font-semibold text-ink">Event Planning</h1>
        </div>
        <span
          role="status"
          className={cn("rounded-xl border px-3 py-1.5 text-sm font-medium", STATE_STYLE[report.overall])}
        >
          Overall: {STATE_LABEL[report.overall]}
        </span>
      </header>

      {/* Readiness */}
      <section className="card p-6" aria-labelledby="readiness-h">
        <h2 id="readiness-h" className="text-base font-semibold text-ink">Operational readiness</h2>
        <p className="mt-0.5 text-sm text-ink-muted">
          Explainable category states from the capabilities Cue supports today — each with its evidence and next action.
        </p>
        <ul className="mt-4 grid list-none gap-3 sm:grid-cols-2">
          {report.categories.map((c) => (
            <li key={c.key} className="rounded-xl border border-line p-4">
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium text-ink">{c.label}</span>
                <span className={cn("shrink-0 rounded-full border px-2 py-0.5 text-xs font-medium", STATE_STYLE[c.state])}>
                  {STATE_LABEL[c.state]}
                </span>
              </div>
              <p className="mt-2 break-words text-sm text-ink-muted"><span className="text-ink-muted/70">Evidence:</span> {c.evidence}</p>
              <p className="mt-1 break-words text-sm text-ink-muted"><span className="text-ink-muted/70">Next:</span> {c.nextAction}</p>
            </li>
          ))}
        </ul>
      </section>

      {/* Capacity vs demand */}
      <section className="card p-6" aria-labelledby="capacity-h">
        <h2 id="capacity-h" className="text-base font-semibold text-ink">Capacity &amp; demand</h2>
        <p className="mt-0.5 text-sm text-ink-muted">
          Capacity is a hard constraint; invited, confirmed, waitlisted, and projected are separate demand signals.
        </p>
        <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
          <Stat label="Capacity" value={demand.capacity ?? "Uncapped"} />
          <Stat label="Invited" value={demand.invited} />
          <Stat label="Confirmed" value={demand.confirmed} />
          <Stat label="Waitlisted" value={demand.waitlisted} />
          <Stat label="Projected" value={demand.projected} />
        </div>
        {demand.warning && (
          <p role="alert" className={cn("mt-4 rounded-xl border px-3 py-2 text-sm", STATE_STYLE[demand.state])}>
            ⚠ {demand.warning}
          </p>
        )}
      </section>

      {/* Ownership */}
      <section className="card p-6" aria-labelledby="owner-h">
        <h2 id="owner-h" className="text-base font-semibold text-ink">Planning ownership</h2>
        <p className="mt-0.5 text-sm text-ink-muted">
          A WorkspaceMember owns planning — distinct from People (relationship records). No production auth yet.
        </p>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <span aria-hidden className="grid h-9 w-9 place-items-center rounded-full border border-line text-xs font-semibold text-ink">
            {me.initials}
          </span>
          <div className="text-sm">
            <div className="text-ink">{me.name}</div>
            <div className="text-ink-muted">{me.role}</div>
          </div>
          {plan.ownerId === me.id ? (
            <Badge className="ml-auto">Owner</Badge>
          ) : (
            <Button variant="outline" className="ml-auto" onClick={() => update({ ownerId: me.id })}>
              Take ownership
            </Button>
          )}
        </div>
      </section>

      {/* Multi-day structure */}
      <Editor
        id="days"
        title="Event structure (days)"
        hint="Structure a multi-day event; days seed from the event's local calendar."
        items={plan.days.map((d) => ({ id: d.id, primary: d.label, secondary: d.date }))}
        onAdd={(v) =>
          v.a &&
          update({ days: [...plan.days, { id: planId("day"), date: v.b || localDate(event.startsAt, event.timezone), label: v.a }] })
        }
        onRemove={(rid) => update({ days: plan.days.filter((d) => d.id !== rid) })}
        fields={[{ key: "a", label: "Day label", placeholder: "Day label (e.g. Arrivals)" }, { key: "b", label: "Date", placeholder: "yyyy-mm-dd", type: "date" }]}
      />

      {/* Milestones */}
      <section className="card p-6" aria-labelledby="ms-h">
        <h2 id="ms-h" className="text-base font-semibold text-ink">Milestones</h2>
        <ul className="mt-3 space-y-2">
          {plan.milestones.map((m) => (
            <li key={m.id} className="flex flex-wrap items-center gap-3 rounded-xl border border-line px-3 py-2 text-sm">
              <button
                className="focus-ring rounded-lg border border-line px-2 py-0.5 text-xs text-ink-muted hover:text-ink"
                aria-label={`Milestone status: ${m.status}. Click to advance.`}
                onClick={() => {
                  const order: MilestoneStatus[] = ["todo", "in-progress", "done"];
                  const next = order[(order.indexOf(m.status) + 1) % order.length];
                  update({ milestones: plan.milestones.map((x) => (x.id === m.id ? { ...x, status: next } : x)) });
                }}
              >
                {m.status}
              </button>
              <span className={cn("min-w-0 flex-1 break-words text-ink", m.status === "done" && "text-ink-muted line-through")}>{m.title}</span>
              {m.dueDate && <span className="text-ink-muted">{m.dueDate}</span>}
              <RemoveButton label={`Remove milestone ${m.title}`} onClick={() => update({ milestones: plan.milestones.filter((x) => x.id !== m.id) })} />
            </li>
          ))}
          {plan.milestones.length === 0 && <li className="text-sm text-ink-muted">No milestones yet.</li>}
        </ul>
        <AddRow
          fields={[{ key: "a", label: "Milestone", placeholder: "Milestone (e.g. Confirm venue)" }, { key: "b", label: "Due date", placeholder: "Due", type: "date" }]}
          onAdd={(v) => v.a && update({ milestones: [...plan.milestones, { id: planId("m"), title: v.a, dueDate: v.b || undefined, status: "todo" }] })}
        />
      </section>

      {/* Responsibilities — explicit, intentional assignment (finding #4) */}
      <section className="card p-6" aria-labelledby="resp-h">
        <h2 id="resp-h" className="text-base font-semibold text-ink">Responsibilities</h2>
        <p className="mt-0.5 text-sm text-ink-muted">Accountability areas — assign or unassign a WorkspaceMember for each.</p>
        <ul className="mt-3 space-y-2">
          {plan.responsibilities.map((r) => (
            <li key={r.id} className="flex flex-wrap items-center gap-3 rounded-xl border border-line px-3 py-2 text-sm">
              <span className="min-w-0 flex-1 break-words text-ink">{r.area}</span>
              <span className={cn("text-xs", r.ownerId ? "text-ink-muted" : "text-amber-300")}>
                {ownerName(r.ownerId)}
              </span>
              {r.ownerId ? (
                <button
                  className="focus-ring rounded-lg border border-line px-2 py-0.5 text-xs text-ink-muted hover:text-ink"
                  onClick={() => update({ responsibilities: plan.responsibilities.map((x) => (x.id === r.id ? { ...x, ownerId: undefined } : x)) })}
                >
                  Unassign
                </button>
              ) : (
                <button
                  className="focus-ring rounded-lg border border-line px-2 py-0.5 text-xs text-ink-muted hover:text-ink"
                  onClick={() => update({ responsibilities: plan.responsibilities.map((x) => (x.id === r.id ? { ...x, ownerId: me.id } : x)) })}
                >
                  Assign to {me.name.split(" ")[0]}
                </button>
              )}
              <RemoveButton label={`Remove responsibility ${r.area}`} onClick={() => update({ responsibilities: plan.responsibilities.filter((x) => x.id !== r.id) })} />
            </li>
          ))}
          {plan.responsibilities.length === 0 && <li className="text-sm text-ink-muted">No responsibilities yet.</li>}
        </ul>
        <AddRow
          fields={[{ key: "a", label: "Responsibility area", placeholder: "Area (e.g. Catering, AV, Comms)" }]}
          onAdd={(v) => v.a && update({ responsibilities: [...plan.responsibilities, { id: planId("r"), area: v.a }] })}
        />
      </section>

      {/* Risks & blockers — with an explicit review acknowledgement (finding #3) */}
      <section className="card p-6" aria-labelledby="risk-h">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="risk-h" className="text-base font-semibold text-ink">Risks &amp; blockers</h2>
          <label className="flex items-center gap-2 text-sm text-ink-muted">
            <input
              type="checkbox"
              className="focus-ring"
              checked={plan.risksReviewed}
              onChange={(e) => update({ risksReviewed: e.target.checked })}
            />
            Risk register reviewed
          </label>
        </div>
        <ul className="mt-3 space-y-2">
          {plan.risks.map((k) => (
            <li key={k.id} className="flex flex-wrap items-center gap-3 rounded-xl border border-line px-3 py-2 text-sm">
              <span className="min-w-0 flex-1 break-words text-ink">{k.title}</span>
              <Badge className={k.severity === "high" ? "text-rose-300" : k.severity === "medium" ? "text-amber-300" : undefined}>{k.severity}</Badge>
              <button
                className={cn("focus-ring rounded-lg border px-2 py-0.5 text-xs", k.blocker ? "border-rose-500/40 text-rose-300" : "border-line text-ink-muted")}
                aria-label={`${k.blocker ? "Blocker" : "Risk"} — click to toggle`}
                onClick={() => update({ risks: plan.risks.map((x) => (x.id === k.id ? { ...x, blocker: !x.blocker } : x)) })}
              >
                {k.blocker ? "blocker" : "risk"}
              </button>
              <button
                className="focus-ring rounded-lg border border-line px-2 py-0.5 text-xs text-ink-muted hover:text-ink"
                aria-label={`Risk status: ${k.status}. Click to advance.`}
                onClick={() => {
                  const order = ["open", "mitigating", "resolved"] as const;
                  const next = order[(order.indexOf(k.status) + 1) % order.length];
                  update({ risks: plan.risks.map((x) => (x.id === k.id ? { ...x, status: next } : x)) });
                }}
              >
                {k.status}
              </button>
              <RemoveButton label={`Remove risk ${k.title}`} onClick={() => update({ risks: plan.risks.filter((x) => x.id !== k.id) })} />
            </li>
          ))}
          {plan.risks.length === 0 && <li className="text-sm text-ink-muted">No risks logged.</li>}
        </ul>
        <RiskAdd onAdd={(title, severity, blocker) => update({ risks: [...plan.risks, { id: planId("k"), title, severity, status: "open", blocker }] })} />
      </section>

      <p className="pb-6 text-center text-xs text-ink-muted">
        Deferred to the next slice: organization-wide templates, reusable venues/rooms, registration, sessions, speakers,
        sponsors, budgets, and weighted readiness percentages.
      </p>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-xl border border-line p-3">
      <div className="text-xs text-ink-muted">{label}</div>
      <div className="mt-0.5 text-lg font-semibold text-ink">{value}</div>
    </div>
  );
}

function RemoveButton({ onClick, label }: { onClick: () => void; label: string }) {
  return (
    <button onClick={onClick} className="focus-ring rounded-lg px-2 py-0.5 text-xs text-ink-muted hover:text-rose-300" aria-label={label}>✕</button>
  );
}

type FieldSpec = { key: "a" | "b"; label: string; placeholder: string; type?: string };

function AddRow({ fields, onAdd }: { fields: FieldSpec[]; onAdd: (v: { a: string; b: string }) => void }) {
  const [v, setV] = useState({ a: "", b: "" });
  return (
    <div className="mt-3 flex flex-wrap gap-2">
      {fields.map((f) => (
        <input
          key={f.key}
          type={f.type ?? "text"}
          value={v[f.key]}
          placeholder={f.placeholder}
          aria-label={f.label}
          onChange={(e) => setV({ ...v, [f.key]: e.target.value })}
          className="focus-ring min-w-[10rem] flex-1 rounded-xl border border-line bg-transparent px-3 py-2 text-sm text-ink placeholder:text-ink-muted"
        />
      ))}
      <Button variant="outline" onClick={() => { onAdd(v); setV({ a: "", b: "" }); }}>Add</Button>
    </div>
  );
}

function Editor({
  id, title, hint, items, fields, onAdd, onRemove,
}: {
  id: string;
  title: string;
  hint: string;
  items: { id: string; primary: string; secondary?: string }[];
  fields: FieldSpec[];
  onAdd: (v: { a: string; b: string }) => void;
  onRemove: (id: string) => void;
}) {
  return (
    <section className="card p-6" aria-labelledby={`${id}-h`}>
      <h2 id={`${id}-h`} className="text-base font-semibold text-ink">{title}</h2>
      <p className="mt-0.5 text-sm text-ink-muted">{hint}</p>
      <ul className="mt-3 space-y-2">
        {items.map((it) => (
          <li key={it.id} className="flex flex-wrap items-center gap-3 rounded-xl border border-line px-3 py-2 text-sm">
            <span className="min-w-0 flex-1 break-words text-ink">{it.primary}</span>
            {it.secondary && <span className="text-ink-muted">{it.secondary}</span>}
            <RemoveButton label={`Remove ${it.primary}`} onClick={() => onRemove(it.id)} />
          </li>
        ))}
        {items.length === 0 && <li className="text-sm text-ink-muted">Nothing yet.</li>}
      </ul>
      <AddRow fields={fields} onAdd={onAdd} />
    </section>
  );
}

function RiskAdd({ onAdd }: { onAdd: (title: string, severity: RiskSeverity, blocker: boolean) => void }) {
  const [title, setTitle] = useState("");
  const [severity, setSeverity] = useState<RiskSeverity>("medium");
  const [blocker, setBlocker] = useState(false);
  return (
    <div className="mt-3 flex flex-wrap items-center gap-2">
      <input
        value={title}
        placeholder="Risk (e.g. Key speaker unconfirmed)"
        aria-label="Risk title"
        onChange={(e) => setTitle(e.target.value)}
        className="focus-ring min-w-[12rem] flex-1 rounded-xl border border-line bg-transparent px-3 py-2 text-sm text-ink placeholder:text-ink-muted"
      />
      <select
        value={severity}
        aria-label="Risk severity"
        onChange={(e) => setSeverity(e.target.value as RiskSeverity)}
        className="focus-ring rounded-xl border border-line bg-transparent px-3 py-2 text-sm text-ink"
      >
        <option value="low">low</option>
        <option value="medium">medium</option>
        <option value="high">high</option>
      </select>
      <label className="flex items-center gap-2 text-sm text-ink-muted">
        <input type="checkbox" className="focus-ring" checked={blocker} onChange={(e) => setBlocker(e.target.checked)} /> blocker
      </label>
      <Button variant="outline" onClick={() => { if (title.trim()) { onAdd(title.trim(), severity, blocker); setTitle(""); setBlocker(false); } }}>Add</Button>
    </div>
  );
}
