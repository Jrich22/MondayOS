import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import type { CueEvent, Guest, GuestRole, RsvpStatus } from "@/lib/types";
import { ALL_ROLES, ROLE_META, RSVP_META, RSVP_ORDER, initials, withCheckIn } from "@/lib/guests-select";
import { addGuest, removeGuest, updateGuest } from "@/lib/guests";
import { personIdForGuest } from "@/lib/people";
import { InvitationSection } from "./InvitationSection";
import { Field, TextInput, TextArea, Select } from "@/components/ui/Field";
import { Switch } from "@/components/ui/Switch";
import { Button } from "@/components/ui/Button";
import { StarIcon, CloseIcon, TrashIcon, CheckIcon, NetworkIcon } from "@/components/icons";
import { cn } from "@/lib/cn";

/**
 * The attendee record editor — a right-hand slide-over that exposes the full CRM
 * schema grouped the way an operator thinks (Identity → Professional →
 * Attendance → Preferences → Communication → Notes → Tags). Edits work on a
 * local draft and commit on Save, so the drawer can be closed without dribbling
 * half-typed values into the store. Used for both adding and editing.
 */
export function GuestDrawer({
  guest,
  event,
  mode,
  onClose,
}: {
  guest: Guest;
  event: CueEvent;
  mode: "edit" | "create";
  onClose: () => void;
}) {
  const [draft, setDraft] = useState<Guest>(guest);
  const dirty = draft !== guest;
  const canSave =
    draft.identity.firstName.trim() !== "" || draft.identity.lastName.trim() !== "";

  // Close on Escape; lock background scroll while open.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [onClose]);

  // --- Draft setters (immutable, section-scoped) ---------------------------
  const set = (patch: Partial<Guest>) => setDraft((d) => ({ ...d, ...patch }));
  const setId = (patch: Partial<Guest["identity"]>) =>
    setDraft((d) => ({ ...d, identity: { ...d.identity, ...patch } }));
  const setPro = (patch: Partial<Guest["professional"]>) =>
    setDraft((d) => ({ ...d, professional: { ...d.professional, ...patch } }));
  const setAtt = (patch: Partial<Guest["attendance"]>) =>
    setDraft((d) => ({ ...d, attendance: { ...d.attendance, ...patch } }));
  const setPref = (patch: Partial<Guest["preferences"]>) =>
    setDraft((d) => ({ ...d, preferences: { ...d.preferences, ...patch } }));
  const setComm = (patch: Partial<Guest["communication"]>) =>
    setDraft((d) => ({ ...d, communication: { ...d.communication, ...patch } }));
  const setNotes = (patch: Partial<Guest["notes"]>) =>
    setDraft((d) => ({ ...d, notes: { ...d.notes, ...patch } }));

  const toggleRole = (role: GuestRole) =>
    setDraft((d) => ({
      ...d,
      roles: d.roles.includes(role)
        ? d.roles.filter((r) => r !== role)
        : [...d.roles, role],
    }));

  const setCheckedIn = (v: boolean) =>
    setDraft((d) => withCheckIn(d, v, new Date().toISOString()));

  const save = () => {
    const stamped: Guest = { ...draft, updatedAt: new Date().toISOString() };
    if (mode === "create") addGuest(stamped);
    else updateGuest(stamped);
    onClose();
  };

  const del = () => {
    removeGuest(guest.id);
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      {/* Scrim */}
      <button
        aria-label="Close attendee"
        onClick={onClose}
        className="absolute inset-0 bg-black/50 backdrop-blur-sm"
      />

      {/* Panel */}
      <div
        role="dialog"
        aria-modal="true"
        aria-label={mode === "create" ? "Add attendee" : "Edit attendee"}
        className="animate-fade-up relative flex h-full w-full max-w-md flex-col border-l border-line bg-canvas-raised shadow-2xl"
      >
        {/* Header */}
        <header className="flex items-start justify-between gap-3 border-b border-line px-5 py-4">
          <div className="flex min-w-0 items-center gap-3">
            <span className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-brand-500/15 text-sm font-semibold text-brand-200">
              {initials(draft)}
            </span>
            <div className="min-w-0">
              <p className="truncate text-base font-semibold text-ink">
                {draft.identity.firstName || draft.identity.lastName
                  ? `${draft.identity.firstName} ${draft.identity.lastName}`.trim()
                  : "New attendee"}
              </p>
              <p className="truncate text-xs text-ink-muted">
                {draft.professional.jobTitle || "Attendee record"}
              </p>
              {mode === "edit" && (
                <Link
                  to={`/people/${personIdForGuest(guest)}`}
                  onClick={onClose}
                  className="focus-ring mt-1 inline-flex items-center gap-1 text-[11px] font-medium text-brand-300 hover:text-brand-200"
                >
                  <NetworkIcon width={12} height={12} />
                  View relationship profile
                </Link>
              )}
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className="focus-ring rounded-lg p-1.5 text-ink-muted hover:bg-white/5 hover:text-ink"
          >
            <CloseIcon width={18} height={18} />
          </button>
        </header>

        {/* Body */}
        <div className="flex-1 space-y-6 overflow-y-auto px-5 py-5">
          <Section title="Identity">
            <div className="grid grid-cols-2 gap-3">
              <Field label="First name" htmlFor="g-first">
                <TextInput
                  id="g-first"
                  autoFocus={mode === "create"}
                  value={draft.identity.firstName}
                  onChange={(e) => setId({ firstName: e.target.value })}
                  placeholder="Ava"
                />
              </Field>
              <Field label="Last name" htmlFor="g-last">
                <TextInput
                  id="g-last"
                  value={draft.identity.lastName}
                  onChange={(e) => setId({ lastName: e.target.value })}
                  placeholder="Chen"
                />
              </Field>
            </div>
            <Field label="Preferred name" htmlFor="g-pref" optional>
              <TextInput
                id="g-pref"
                value={draft.identity.preferredName ?? ""}
                onChange={(e) => setId({ preferredName: e.target.value || undefined })}
                placeholder="Goes by…"
              />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Email" htmlFor="g-email" optional>
                <TextInput
                  id="g-email"
                  type="email"
                  value={draft.identity.email ?? ""}
                  onChange={(e) => setId({ email: e.target.value || undefined })}
                  placeholder="ava@…"
                />
              </Field>
              <Field label="Phone" htmlFor="g-phone" optional>
                <TextInput
                  id="g-phone"
                  value={draft.identity.phone ?? ""}
                  onChange={(e) => setId({ phone: e.target.value || undefined })}
                  placeholder="+1…"
                />
              </Field>
            </div>
          </Section>

          <Section title="Professional">
            <div className="grid grid-cols-2 gap-3">
              <Field label="Company" htmlFor="g-co" optional>
                <TextInput
                  id="g-co"
                  value={draft.professional.company ?? ""}
                  onChange={(e) => setPro({ company: e.target.value || undefined })}
                  placeholder="Company"
                />
              </Field>
              <Field label="Job title" htmlFor="g-title" optional>
                <TextInput
                  id="g-title"
                  value={draft.professional.jobTitle ?? ""}
                  onChange={(e) => setPro({ jobTitle: e.target.value || undefined })}
                  placeholder="Title"
                />
              </Field>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Department" htmlFor="g-dept" optional>
                <TextInput
                  id="g-dept"
                  value={draft.professional.department ?? ""}
                  onChange={(e) => setPro({ department: e.target.value || undefined })}
                  placeholder="Team"
                />
              </Field>
              <Field label="Portfolio company" htmlFor="g-port" optional>
                <Select
                  id="g-port"
                  value={draft.professional.portfolioCompanyId ?? ""}
                  onChange={(e) =>
                    setPro({ portfolioCompanyId: e.target.value || undefined })
                  }
                >
                  <option value="">None</option>
                  {event.portfolio.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </Select>
              </Field>
            </div>

            <Field label="Roles">
              <div className="flex flex-wrap gap-1.5">
                {ALL_ROLES.map((role) => {
                  const on = draft.roles.includes(role);
                  return (
                    <button
                      key={role}
                      type="button"
                      onClick={() => toggleRole(role)}
                      aria-pressed={on}
                      className={cn(
                        "focus-ring rounded-lg border px-2.5 py-1 text-xs font-medium transition-colors",
                        on
                          ? "border-brand-500/40 bg-brand-500/15 text-brand-200"
                          : "border-line text-ink-muted hover:text-ink",
                      )}
                    >
                      {ROLE_META[role].label}
                    </button>
                  );
                })}
              </div>
            </Field>

            <ToggleRow
              label="VIP"
              hint="Flags the attendee across filters and sorting"
              checked={draft.vip}
              onChange={(v) => set({ vip: v })}
              icon={<StarIcon width={16} height={16} className={draft.vip ? "text-status-draft" : ""} />}
            />
          </Section>

          <Section title="Attendance">
            <div className="grid grid-cols-2 gap-3">
              <Field label="RSVP" htmlFor="g-rsvp">
                <Select
                  id="g-rsvp"
                  value={draft.attendance.rsvp}
                  onChange={(e) => setAtt({ rsvp: e.target.value as RsvpStatus })}
                >
                  {RSVP_ORDER.map((r) => (
                    <option key={r} value={r}>
                      {RSVP_META[r].label}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Plus ones" htmlFor="g-plus" optional>
                <TextInput
                  id="g-plus"
                  type="number"
                  min={0}
                  value={String(draft.attendance.plusOnes)}
                  onChange={(e) =>
                    setAtt({ plusOnes: Math.max(0, Number(e.target.value) || 0) })
                  }
                />
              </Field>
            </div>
            <ToggleRow
              label="Checked in"
              hint={
                draft.attendance.checkedIn && draft.attendance.checkedInAt
                  ? `Arrived ${new Date(draft.attendance.checkedInAt).toLocaleTimeString([], {
                      hour: "numeric",
                      minute: "2-digit",
                    })}`
                  : "Mark arrival at the door"
              }
              checked={draft.attendance.checkedIn}
              onChange={setCheckedIn}
              icon={<CheckIcon width={16} height={16} className={draft.attendance.checkedIn ? "text-status-live" : ""} />}
            />
            <ToggleRow
              label="Waitlisted"
              checked={draft.attendance.waitlisted}
              onChange={(v) => setAtt({ waitlisted: v })}
            />
            <ToggleRow
              label="No show"
              hint="Confirmed but never arrived"
              checked={draft.attendance.noShow}
              onChange={(v) => setAtt({ noShow: v })}
            />
          </Section>

          {mode === "edit" && (
            <Section title="Invitation & RSVP link">
              <InvitationSection guest={guest} event={event} />
            </Section>
          )}

          <Section title="Preferences">
            <Field label="Dietary restrictions" htmlFor="g-diet" optional>
              <TextInput
                id="g-diet"
                value={draft.preferences.dietary ?? ""}
                onChange={(e) => setPref({ dietary: e.target.value || undefined })}
                placeholder="Vegetarian, allergies…"
              />
            </Field>
            <Field label="Accessibility needs" htmlFor="g-a11y" optional>
              <TextInput
                id="g-a11y"
                value={draft.preferences.accessibility ?? ""}
                onChange={(e) => setPref({ accessibility: e.target.value || undefined })}
                placeholder="Step-free access, interpreter…"
              />
            </Field>
            <Field label="Seating preference" htmlFor="g-seat" optional>
              <TextInput
                id="g-seat"
                value={draft.preferences.seatingPreference ?? ""}
                onChange={(e) =>
                  setPref({ seatingPreference: e.target.value || undefined })
                }
                placeholder="Near the stage…"
              />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <ToggleRow
                label="Hotel needed"
                checked={draft.preferences.hotelNeeded}
                onChange={(v) => setPref({ hotelNeeded: v })}
              />
              <ToggleRow
                label="Transport needed"
                checked={draft.preferences.transportationNeeded}
                onChange={(v) => setPref({ transportationNeeded: v })}
              />
            </div>
          </Section>

          <Section title="Communication">
            <ToggleRow
              label="Invitation sent"
              checked={draft.communication.invitationSent}
              onChange={(v) => setComm({ invitationSent: v })}
            />
            <ToggleRow
              label="Reminder sent"
              checked={draft.communication.reminderSent}
              onChange={(v) => setComm({ reminderSent: v })}
            />
            <ToggleRow
              label="Thank you sent"
              checked={draft.communication.thankYouSent}
              onChange={(v) => setComm({ thankYouSent: v })}
            />
          </Section>

          <Section title="Notes">
            <Field label="Internal notes" htmlFor="g-inote" hint="Only your team sees this" optional>
              <TextArea
                id="g-inote"
                rows={3}
                value={draft.notes.internal ?? ""}
                onChange={(e) => setNotes({ internal: e.target.value || undefined })}
                placeholder="Context for the ops team…"
              />
            </Field>
            <Field label="Public notes" htmlFor="g-pnote" optional>
              <TextArea
                id="g-pnote"
                rows={2}
                value={draft.notes.public ?? ""}
                onChange={(e) => setNotes({ public: e.target.value || undefined })}
                placeholder="Shareable note…"
              />
            </Field>
          </Section>

          <Section title="Tags">
            <TagInput
              tags={draft.tags}
              onChange={(tags) => set({ tags })}
            />
          </Section>

          {mode === "edit" && (
            <div className="pt-2">
              <button
                onClick={del}
                className="focus-ring inline-flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm font-medium text-red-400 hover:bg-red-500/10"
              >
                <TrashIcon width={16} height={16} />
                Remove attendee
              </button>
            </div>
          )}
        </div>

        {/* Footer */}
        <footer className="flex items-center justify-between gap-3 border-t border-line px-5 py-3.5">
          <span className="text-xs text-ink-faint">
            {mode === "create"
              ? "Add to roster"
              : dirty
                ? "Unsaved changes"
                : "All changes saved"}
          </span>
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button
              onClick={save}
              disabled={!canSave || (mode === "edit" && !dirty)}
              className={cn((!canSave || (mode === "edit" && !dirty)) && "opacity-50")}
            >
              {mode === "create" ? "Add attendee" : "Save changes"}
            </Button>
          </div>
        </footer>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-3">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-ink-faint">{title}</h4>
      {children}
    </section>
  );
}

function ToggleRow({
  label,
  hint,
  checked,
  onChange,
  icon,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  icon?: ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-xl border border-line px-3 py-2">
      <div className="flex min-w-0 items-center gap-2">
        {icon && <span className="shrink-0 text-ink-muted">{icon}</span>}
        <div className="min-w-0">
          <p className="text-sm font-medium text-ink">{label}</p>
          {hint && <p className="truncate text-xs text-ink-faint">{hint}</p>}
        </div>
      </div>
      <Switch checked={checked} onChange={onChange} label={label} />
    </div>
  );
}

function TagInput({ tags, onChange }: { tags: string[]; onChange: (tags: string[]) => void }) {
  const [value, setValue] = useState("");

  const add = () => {
    const t = value.trim();
    if (t && !tags.includes(t)) onChange([...tags, t]);
    setValue("");
  };

  return (
    <div>
      <div className="mb-2 flex flex-wrap gap-1.5">
        {tags.length === 0 && <span className="text-xs text-ink-faint">No tags yet</span>}
        {tags.map((t) => (
          <span
            key={t}
            className="inline-flex items-center gap-1 rounded-full border border-line bg-white/[0.04] px-2.5 py-0.5 text-xs font-medium text-ink-muted"
          >
            {t}
            <button
              type="button"
              aria-label={`Remove ${t}`}
              onClick={() => onChange(tags.filter((x) => x !== t))}
              className="text-ink-faint hover:text-ink"
            >
              <CloseIcon width={12} height={12} />
            </button>
          </span>
        ))}
      </div>
      <TextInput
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            add();
          }
        }}
        onBlur={add}
        placeholder="Add a tag, press Enter…"
      />
    </div>
  );
}
