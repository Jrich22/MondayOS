/**
 * Manual capture form — the operator records someone they reviewed themselves.
 *
 * Duplicate detection runs as the name and company are typed, so the warning
 * appears while the operator can still act on it. Reuse is offered, never
 * applied: auto-merging on a name match would silently fuse two different
 * humans, which is far worse than carrying a duplicate until someone notices.
 */
import { useMemo, useState, type FC } from "react";
import type { Candidate, SourcingBrief } from "@/lib/types";
import type { CaptureInput, DuplicateResolution } from "@/lib/capture";
import { findDuplicatesFor } from "@/lib/capture";
import { currentCompany } from "@/lib/candidate";
import { TextAreaField, TextField } from "@/components/authoring/Fields";
import { cn } from "@/components/ui/Primitives";

const EMPTY: CaptureInput = {
  fullName: "",
  headline: "",
  location: "",
  currentTitle: "",
  currentCompany: "",
  linkedInUrl: "",
  rationale: "",
  personNotes: "",
  skills: [],
};

export const CaptureForm: FC<{
  brief: SourcingBrief | undefined;
  pool: Candidate[];
  disabled: boolean;
  onCapture: (input: CaptureInput, resolution: DuplicateResolution) => void;
  onSkip: (name: string, reason: string, closeCall: boolean) => void;
}> = ({ brief, pool, disabled, onCapture, onSkip }) => {
  const [input, setInput] = useState<CaptureInput>(EMPTY);
  const [assessments, setAssessments] = useState<Record<string, "yes" | "no" | "unknown">>({});
  const [reuseId, setReuseId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [skipReason, setSkipReason] = useState("");
  const [closeCall, setCloseCall] = useState(false);

  const set = (patch: Partial<CaptureInput>) => setInput((p) => ({ ...p, ...patch }));

  const duplicates = useMemo(
    () => (input.fullName.trim() ? findDuplicatesFor(input, pool) : []),
    [input, pool],
  );

  const reset = () => {
    setInput(EMPTY);
    setAssessments({});
    setReuseId(null);
    setError("");
    setSkipReason("");
    setCloseCall(false);
  };

  const capture = () => {
    if (!input.fullName.trim()) {
      setError("A candidate needs a name.");
      return;
    }
    const withAssessments: CaptureInput = {
      ...input,
      assessments: Object.entries(assessments).map(([requirementId, met]) => ({
        requirementId,
        met,
      })),
    };
    onCapture(
      withAssessments,
      reuseId ? { kind: "existing", candidateId: reuseId } : { kind: "new" },
    );
    reset();
  };

  const skip = () => {
    if (!input.fullName.trim()) {
      setError("Name who you skipped, or the record means nothing.");
      return;
    }
    onSkip(input.fullName, skipReason, closeCall);
    reset();
  };

  return (
    <div className={cn("space-y-5", disabled && "pointer-events-none opacity-50")}>
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          id="cap-name"
          label="Full name"
          value={input.fullName}
          placeholder="Priya Raman"
          onChange={(v) => set({ fullName: v })}
        />
        <TextField
          id="cap-headline"
          label="Headline"
          value={input.headline ?? ""}
          placeholder="Staff Infrastructure Engineer"
          onChange={(v) => set({ headline: v })}
        />
        <TextField
          id="cap-title"
          label="Current title"
          value={input.currentTitle ?? ""}
          onChange={(v) => set({ currentTitle: v })}
        />
        <TextField
          id="cap-company"
          label="Current company"
          value={input.currentCompany ?? ""}
          onChange={(v) => set({ currentCompany: v })}
        />
        <TextField
          id="cap-location"
          label="Location"
          value={input.location ?? ""}
          onChange={(v) => set({ location: v })}
        />
        <TextField
          id="cap-email"
          label="Email (optional)"
          value={input.email ?? ""}
          onChange={(v) => set({ email: v })}
        />
      </div>

      <TextField
        id="cap-url"
        label="Profile URL"
        hint="Recorded only because you opened and reviewed this profile yourself."
        value={input.linkedInUrl ?? ""}
        onChange={(v) => set({ linkedInUrl: v })}
      />

      {duplicates.length > 0 && (
        <div
          role="alert"
          className="rounded-xl border border-oversight-line bg-oversight-soft p-3"
        >
          <p className="text-xs font-semibold uppercase tracking-wide text-oversight">
            {duplicates.length === 1 ? "Possible duplicate" : "Possible duplicates"}
          </p>
          <p className="mt-1 text-xs text-ink-muted">
            Someone matching this person is already in the talent pool. Reuse them so their
            history stays in one place — or continue as a new person if this is someone else.
          </p>
          <ul className="mt-2 space-y-1.5">
            {duplicates.map((d) => (
              <li key={d.id} className="flex items-center justify-between gap-3">
                <span className="text-sm text-ink">
                  {d.fullName}
                  <span className="ml-1 text-xs text-ink-faint">
                    {d.headline}
                    {currentCompany(d) && ` · ${currentCompany(d)}`}
                  </span>
                </span>
                <button
                  type="button"
                  onClick={() => setReuseId(reuseId === d.id ? null : d.id)}
                  aria-pressed={reuseId === d.id}
                  className={cn(
                    "shrink-0 rounded-lg border px-2.5 py-1 text-xs transition-colors",
                    reuseId === d.id
                      ? "border-brand-500/40 bg-brand-500/15 text-brand-200"
                      : "border-line text-ink-muted hover:text-ink",
                  )}
                >
                  {reuseId === d.id ? "Reusing" : "Reuse"}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {brief && brief.requirements.length > 0 && (
        <div>
          <p className="mb-2 text-sm font-medium text-ink">Assessment</p>
          <ul className="space-y-1.5">
            {brief.requirements.map((r) => (
              <li
                key={r.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-line bg-canvas-overlay px-3 py-2"
              >
                <span className="text-sm text-ink-muted">
                  {r.label}
                  <span className="ml-1 text-xs text-ink-faint">
                    {r.kind === "required" ? "must-have" : `nice ·${r.weight}`}
                  </span>
                </span>
                <div role="group" aria-label={`Assessment for ${r.label}`} className="flex gap-1">
                  {(["yes", "unknown", "no"] as const).map((v) => (
                    <button
                      key={v}
                      type="button"
                      aria-pressed={assessments[r.id] === v}
                      onClick={() =>
                        setAssessments((p) => ({ ...p, [r.id]: p[r.id] === v ? "unknown" : v }))
                      }
                      className={cn(
                        "rounded-md border px-2 py-0.5 text-xs capitalize transition-colors",
                        assessments[r.id] === v
                          ? v === "yes"
                            ? "border-stage-advanced/40 bg-stage-advanced/15 text-stage-advanced"
                            : v === "no"
                              ? "border-stage-rejected/40 bg-stage-rejected/15 text-stage-rejected"
                              : "border-line-strong bg-white/5 text-ink"
                          : "border-line text-ink-faint hover:text-ink-muted",
                      )}
                    >
                      {v}
                    </button>
                  ))}
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      <TextAreaField
        id="cap-rationale"
        label="Fit rationale"
        hint="Why this person, for THIS req. Stored on the evaluation, not the person."
        rows={3}
        value={input.rationale ?? ""}
        onChange={(v) => set({ rationale: v })}
      />

      <TextAreaField
        id="cap-notes"
        label="Notes about the person"
        hint="Durable across requisitions — stored on their persistent record."
        rows={3}
        value={input.personNotes ?? ""}
        onChange={(v) => set({ personNotes: v })}
      />

      {error && (
        <p role="alert" className="text-sm text-stage-rejected">
          {error}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={capture}
          className="rounded-lg bg-brand-600 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-500"
        >
          {reuseId ? "Add existing person to req" : "Add to requisition"}
        </button>
        <span className="text-xs text-ink-faint">or</span>
        <input
          type="text"
          aria-label="Skip reason"
          value={skipReason}
          placeholder="Reason for skipping"
          onChange={(e) => setSkipReason(e.target.value)}
          className="min-w-[12rem] flex-1 rounded-lg border border-line bg-canvas px-3 py-2 text-sm text-ink placeholder:text-ink-faint focus:border-brand-500 focus:outline-none"
        />
        <label className="flex items-center gap-1.5 text-xs text-ink-muted">
          <input
            type="checkbox"
            checked={closeCall}
            onChange={(e) => setCloseCall(e.target.checked)}
            className="accent-brand-500"
          />
          Close call
        </label>
        <button
          type="button"
          onClick={skip}
          className="rounded-lg border border-line px-3 py-2 text-sm text-ink-muted transition-colors hover:border-line-strong hover:text-ink"
        >
          Skip
        </button>
      </div>
    </div>
  );
};
