/**
 * Req authoring — the recruiter's command center for one requisition.
 *
 * Progressive sections rather than one long form: a sourcing req has ~20 fields
 * and presenting them as a single scroll makes every one feel equally urgent.
 * The rail carries per-section progress so the recruiter can see what is left
 * without reading it.
 *
 * Edits are held in local state and autosaved on a debounce (see AUTOSAVE_MS).
 * Local state is the working copy and the store is the durable one; the save
 * indicator reports which is ahead, so "unsaved" is a fact rather than a guess.
 *
 * Uses the existing Req and SourcingBrief models exclusively — no competing
 * authoring model. Domain rules (lifecycle transitions, brief versioning,
 * readiness) stay in lib/; this file is presentation and edit plumbing.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type FC } from "react";
import { Link, useParams } from "react-router-dom";
import type { Req, SourcingBrief } from "@/lib/types";
import { useWorkspace, reqWorkspace, saveReqDraft, updateReq as storeUpdateReq } from "@/lib/store";
import { hasUnsavedChanges, transition, updateReq, type ReqEdits } from "@/lib/req";
import {
  addRequirement,
  addToList,
  removeFromList,
  removeRequirement,
  reviseBrief,
  type BriefListField,
} from "@/lib/brief";
import { canOpenForSourcing, evaluateReadiness, type SectionId } from "@/lib/readiness";
import { ReadinessPanel } from "@/components/authoring/ReadinessPanel";
import {
  NumberField,
  SelectField,
  TagField,
  TextAreaField,
  TextField,
} from "@/components/authoring/Fields";
import { Card, EmptyState, ReqStatusBadge, cn } from "@/components/ui/Primitives";

/** Debounce before an edit is persisted. Long enough not to thrash a keystroke. */
export const AUTOSAVE_MS = 800;

const SECTIONS: Array<{ id: SectionId; label: string }> = [
  { id: "role", label: "Role basics" },
  { id: "description", label: "Job description" },
  { id: "intake", label: "Intake notes" },
  { id: "targeting", label: "Targeting" },
  { id: "requirements", label: "Must-haves & nice-to-haves" },
  { id: "keywords", label: "Keywords & experience" },
  { id: "goals", label: "Sourcing goals" },
];

const SENIORITY = [
  { value: "junior", label: "Junior" },
  { value: "mid", label: "Mid" },
  { value: "senior", label: "Senior" },
  { value: "staff", label: "Staff" },
  { value: "principal", label: "Principal" },
  { value: "executive", label: "Executive" },
] as const;

const WORK_MODEL = [
  { value: "onsite", label: "Onsite" },
  { value: "hybrid", label: "Hybrid" },
  { value: "remote", label: "Remote" },
] as const;

const SaveState: FC<{ req: Req; saving: boolean }> = ({ req, saving }) => {
  const label = saving
    ? "Saving…"
    : hasUnsavedChanges(req)
      ? "Unsaved changes"
      : req.lastSavedAt
        ? "All changes saved"
        : "Draft";
  return (
    <span
      role="status"
      aria-live="polite"
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs",
        saving
          ? "border-brand-500/30 bg-brand-500/10 text-brand-200"
          : hasUnsavedChanges(req)
            ? "border-oversight-line bg-oversight-soft text-oversight"
            : "border-line text-ink-faint",
      )}
    >
      {label}
    </span>
  );
};

const ReqAuthoring: FC = () => {
  const { reqId = "" } = useParams();
  useWorkspace(); // re-render when the store changes elsewhere

  const loaded = reqWorkspace(reqId);
  const [req, setReq] = useState<Req | null>(loaded?.req ?? null);
  const [brief, setBrief] = useState<SourcingBrief | undefined>(loaded?.brief);
  const [active, setActive] = useState<SectionId>("role");
  const [saving, setSaving] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Adopt a different req when the route changes (multi-req navigation).
  useEffect(() => {
    const next = reqWorkspace(reqId);
    setReq(next?.req ?? null);
    setBrief(next?.brief);
    setActive("role");
  }, [reqId]);

  const flush = useCallback((r: Req, b: SourcingBrief | undefined) => {
    setSaving(true);
    const saved = saveReqDraft(r, b);
    setReq(saved);
    setSaving(false);
  }, []);

  /** Stage an edit locally and schedule the write. */
  const stage = useCallback(
    (nextReq: Req, nextBrief: SourcingBrief | undefined) => {
      setReq(nextReq);
      setBrief(nextBrief);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => flush(nextReq, nextBrief), AUTOSAVE_MS);
    },
    [flush],
  );

  // Never lose a pending edit on unmount — the debounce must not outlive it.
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  const readiness = useMemo(
    () => (req ? evaluateReadiness(req, brief) : null),
    [req, brief],
  );

  if (!req || !readiness) {
    return (
      <div className="mx-auto max-w-3xl">
        <EmptyState
          title="Requisition not found"
          body="This requisition may have been removed."
        />
        <div className="mt-4 text-center">
          <Link to="/" className="text-sm text-brand-400 hover:underline">
            Back to workspace
          </Link>
        </div>
      </div>
    );
  }

  const editReq = (edits: ReqEdits) => stage(updateReq(req, edits), brief);
  const editBrief = (changes: Parameters<typeof reviseBrief>[1]) => {
    if (!brief) return;
    stage(updateReq(req, {}), reviseBrief(brief, changes));
  };
  const editList = (field: BriefListField, fn: typeof addToList, value: string) => {
    if (!brief) return;
    stage(updateReq(req, {}), fn(brief, field, value));
  };

  const openCheck = canOpenForSourcing(req, brief);

  const openForSourcing = () => {
    if (!openCheck.allowed) return;
    const opened = transition(req, "open");
    storeUpdateReq(opened);
    flush(opened, brief);
  };

  const saveNow = () => {
    if (timer.current) clearTimeout(timer.current);
    flush(req, brief);
  };

  const sectionProps = (id: SectionId) => ({
    id: `section-${id}`,
    hidden: active !== id,
    "aria-labelledby": `tab-${id}`,
    role: "tabpanel" as const,
  });

  return (
    <div className="mx-auto max-w-6xl">
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <Link to="/" className="text-sm text-ink-muted hover:text-ink">
            ← Workspace
          </Link>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs text-ink-faint">{req.code || "unassigned"}</span>
            <ReqStatusBadge status={req.status} />
            <SaveState req={req} saving={saving} />
          </div>
          <h1 className="mt-1 truncate text-2xl font-semibold tracking-tight text-ink">
            {req.title || "Untitled requisition"}
          </h1>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={saveNow}
            className="rounded-lg border border-line px-3 py-2 text-sm text-ink-muted transition-colors hover:border-line-strong hover:text-ink"
          >
            Save draft
          </button>
          <button
            type="button"
            onClick={openForSourcing}
            disabled={!openCheck.allowed}
            title={openCheck.allowed ? undefined : openCheck.reasons[0]}
            className={cn(
              "rounded-lg px-3 py-2 text-sm font-medium transition-colors",
              openCheck.allowed
                ? "bg-brand-600 text-white hover:bg-brand-500"
                : "cursor-not-allowed border border-line text-ink-faint",
            )}
          >
            Open for sourcing
          </button>
          <Link
            to={`/reqs/${req.id}`}
            className="rounded-lg border border-line px-3 py-2 text-sm text-ink-muted transition-colors hover:text-ink"
          >
            View pipeline
          </Link>
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-[18rem_minmax(0,1fr)]">
        <aside className="lg:order-1">
          <ReadinessPanel readiness={readiness} activeSection={active} onJump={setActive} />
        </aside>

        <section className="lg:order-2">
          <div role="tablist" aria-label="Authoring sections" className="mb-4 flex flex-wrap gap-1.5">
            {SECTIONS.map((s) => (
              <button
                key={s.id}
                id={`tab-${s.id}`}
                role="tab"
                type="button"
                aria-selected={active === s.id}
                aria-controls={`section-${s.id}`}
                onClick={() => setActive(s.id)}
                className={cn(
                  "rounded-lg px-3 py-1.5 text-xs transition-colors",
                  active === s.id
                    ? "bg-brand-500/15 text-brand-200 ring-1 ring-brand-500/30"
                    : "text-ink-muted hover:bg-white/5 hover:text-ink",
                )}
              >
                {s.label}
              </button>
            ))}
          </div>

          <Card className="p-5">
            {/* ── Role basics ─────────────────────────────────────────── */}
            <div {...sectionProps("role")} className="grid gap-4 sm:grid-cols-2">
              <TextField
                id="f-code"
                label="Requisition code"
                value={req.code}
                placeholder="REQ-014"
                onChange={(v) => editReq({ code: v })}
              />
              <TextField
                id="f-title"
                label="Role title"
                value={req.title}
                placeholder="Staff Platform Engineer"
                onChange={(v) => editReq({ title: v })}
              />
              <TextField
                id="f-team"
                label="Owning team"
                value={req.team}
                placeholder="Infrastructure"
                onChange={(v) => editReq({ team: v })}
              />
              <TextField
                id="f-location"
                label="Primary location"
                value={req.location}
                placeholder="Boston, MA"
                onChange={(v) => editReq({ location: v })}
              />
              <SelectField
                id="f-workmodel"
                label="Work model"
                value={req.workModel}
                options={WORK_MODEL}
                onChange={(v) => editReq({ workModel: v as Req["workModel"] })}
              />
              <NumberField
                id="f-openings"
                label="Openings"
                value={req.openings}
                min={1}
                onChange={(v) => editReq({ openings: v ?? 1 })}
              />
            </div>

            {/* ── Job description ─────────────────────────────────────── */}
            <div {...sectionProps("description")}>
              <TextAreaField
                id="f-jd"
                label="Full job description"
                hint="What the hiring team would publish. Used as the source of truth for the role."
                rows={16}
                value={req.jobDescription ?? ""}
                placeholder="Responsibilities, scope, team context, technologies…"
                onChange={(v) => editReq({ jobDescription: v })}
              />
            </div>

            {/* ── Intake notes ────────────────────────────────────────── */}
            <div {...sectionProps("intake")} className="space-y-4">
              <TextField
                id="f-hm"
                label="Hiring manager"
                value={req.hiringManager}
                placeholder="Dana Whitfield"
                onChange={(v) => editReq({ hiringManager: v })}
              />
              <TextAreaField
                id="f-intake"
                label="Intake notes"
                hint="What the hiring manager actually said — trade-offs, dealbreakers, the profile that would impress them."
                rows={10}
                value={req.intakeNotes ?? ""}
                onChange={(v) => editReq({ intakeNotes: v })}
              />
            </div>

            {/* ── Targeting ───────────────────────────────────────────── */}
            <div {...sectionProps("targeting")} className="space-y-5">
              <TagField
                id="f-locations"
                label="Target locations"
                values={brief?.locations ?? []}
                onAdd={(v) => editList("locations", addToList, v)}
                onRemove={(v) => editList("locations", removeFromList, v)}
              />
              <TagField
                id="f-target-industries"
                label="Target industries"
                values={brief?.targetIndustries ?? []}
                onAdd={(v) => editList("targetIndustries", addToList, v)}
                onRemove={(v) => editList("targetIndustries", removeFromList, v)}
              />
              <TagField
                id="f-target-companies"
                label="Target companies"
                values={brief?.targetCompanies ?? []}
                onAdd={(v) => editList("targetCompanies", addToList, v)}
                onRemove={(v) => editList("targetCompanies", removeFromList, v)}
              />
              <TagField
                id="f-excluded-industries"
                label="Excluded industries"
                tone="exclude"
                values={brief?.excludedIndustries ?? []}
                onAdd={(v) => editList("excludedIndustries", addToList, v)}
                onRemove={(v) => editList("excludedIndustries", removeFromList, v)}
              />
              <TagField
                id="f-excluded-companies"
                label="Excluded companies"
                hint="Conflicts, portfolio companies, do-not-approach."
                tone="exclude"
                values={brief?.excludedCompanies ?? []}
                onAdd={(v) => editList("excludedCompanies", addToList, v)}
                onRemove={(v) => editList("excludedCompanies", removeFromList, v)}
              />
            </div>

            {/* ── Requirements ────────────────────────────────────────── */}
            <div {...sectionProps("requirements")} className="space-y-5">
              <TextField
                id="f-headline"
                label="Search headline"
                hint="One line describing who this search is for."
                value={brief?.headline ?? ""}
                placeholder="Platform engineers who have owned multi-tenant infrastructure"
                onChange={(v) => editBrief({ headline: v })}
              />
              <RequirementEditor
                brief={brief}
                kind="required"
                label="Must-haves"
                hint="Disqualifying if absent. A req with none cannot discriminate between candidates."
                onAdd={(label) => brief && stage(updateReq(req, {}), addRequirement(brief, { label, kind: "required", weight: 5 }))}
                onRemove={(id) => brief && stage(updateReq(req, {}), removeRequirement(brief, id))}
              />
              <RequirementEditor
                brief={brief}
                kind="preferred"
                label="Nice-to-haves"
                hint="Weighted, never blocking. These are what rank candidates against each other."
                onAdd={(label) => brief && stage(updateReq(req, {}), addRequirement(brief, { label, kind: "preferred", weight: 3 }))}
                onRemove={(id) => brief && stage(updateReq(req, {}), removeRequirement(brief, id))}
              />
            </div>

            {/* ── Keywords & experience ───────────────────────────────── */}
            <div {...sectionProps("keywords")} className="space-y-5">
              <TagField
                id="f-keywords"
                label="Search keywords"
                values={brief?.keywords ?? []}
                onAdd={(v) => editList("keywords", addToList, v)}
                onRemove={(v) => editList("keywords", removeFromList, v)}
              />
              <SelectField
                id="f-seniority"
                label="Seniority band"
                value={brief?.seniority ?? "mid"}
                options={SENIORITY}
                onChange={(v) => editBrief({ seniority: v as SourcingBrief["seniority"] })}
              />
              <TextAreaField
                id="f-experience"
                label="Experience guidance"
                hint="The nuance a seniority band cannot carry."
                rows={5}
                value={brief?.experienceGuidance ?? ""}
                placeholder="Depth over breadth — eight years on one hard problem beats fifteen across five teams."
                onChange={(v) => editBrief({ experienceGuidance: v })}
              />
              <TextAreaField
                id="f-outreach"
                label="Outreach angle"
                hint="What makes this role worth a reply."
                rows={4}
                value={brief?.outreachAngle ?? ""}
                onChange={(v) => editBrief({ outreachAngle: v })}
              />
            </div>

            {/* ── Sourcing goals ──────────────────────────────────────── */}
            <div {...sectionProps("goals")} className="space-y-4">
              <div className="grid gap-4 sm:grid-cols-2">
                <NumberField
                  id="f-goal-candidates"
                  label="Target candidates sourced"
                  value={req.sourcingGoals?.targetCandidates}
                  onChange={(v) =>
                    editReq({ sourcingGoals: { ...req.sourcingGoals, targetCandidates: v } })
                  }
                />
                <NumberField
                  id="f-goal-contacts"
                  label="Target candidates contacted"
                  value={req.sourcingGoals?.targetContacts}
                  onChange={(v) =>
                    editReq({ sourcingGoals: { ...req.sourcingGoals, targetContacts: v } })
                  }
                />
              </div>
              <TextAreaField
                id="f-goal-notes"
                label="Goal notes"
                rows={4}
                value={req.sourcingGoals?.notes ?? ""}
                placeholder="Two strong staff-level profiles by end of month."
                onChange={(v) => editReq({ sourcingGoals: { ...req.sourcingGoals, notes: v } })}
              />
            </div>
          </Card>
        </section>
      </div>
    </div>
  );
};

const RequirementEditor: FC<{
  brief: SourcingBrief | undefined;
  kind: "required" | "preferred";
  label: string;
  hint: string;
  onAdd: (label: string) => void;
  onRemove: (id: string) => void;
}> = ({ brief, kind, label, hint, onAdd, onRemove }) => {
  const [draft, setDraft] = useState("");
  const items = (brief?.requirements ?? []).filter((r) => r.kind === kind);
  const inputId = `f-req-${kind}`;

  const commit = () => {
    if (draft.trim()) {
      onAdd(draft.trim());
      setDraft("");
    }
  };

  return (
    <div className="space-y-1.5">
      <label htmlFor={inputId} className="block text-sm font-medium text-ink">
        {label}
      </label>
      <p className="text-xs text-ink-faint">{hint}</p>
      <input
        id={inputId}
        type="text"
        value={draft}
        placeholder="Type and press Enter"
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            commit();
          }
        }}
        className="w-full rounded-lg border border-line bg-canvas px-3 py-2 text-sm text-ink placeholder:text-ink-faint focus:border-brand-500 focus:outline-none"
      />
      {items.length > 0 && (
        <ul className="mt-2 space-y-1.5">
          {items.map((r) => (
            <li
              key={r.id}
              className="flex items-center justify-between gap-3 rounded-lg border border-line bg-canvas-overlay px-3 py-2"
            >
              <span className="flex items-start gap-2 text-sm text-ink-muted">
                <span
                  aria-hidden
                  className={cn(
                    "mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full",
                    kind === "required" ? "bg-brand-400" : "bg-ink-faint",
                  )}
                />
                {r.label}
              </span>
              <button
                type="button"
                aria-label={`Remove ${r.label}`}
                onClick={() => onRemove(r.id)}
                className="shrink-0 text-xs text-ink-faint transition-colors hover:text-stage-rejected"
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

export default ReqAuthoring;
