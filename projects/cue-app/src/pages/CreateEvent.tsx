import { useMemo, useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import {
  emptyDraft,
  validateDraft,
  draftToEvent,
  TIMEZONES,
  type EventDraft,
  type DraftErrors,
} from "@/lib/create";
import { createEvent } from "@/lib/store";
import { currentUser } from "@/lib/session";
import { SectionCard } from "@/components/ui/SectionCard";
import { Field, TextInput, TextArea, Select } from "@/components/ui/Field";
import { Switch } from "@/components/ui/Switch";
import { Button } from "@/components/ui/Button";
import { ClassificationPicker } from "@/components/create/ClassificationPicker";
import { BrandingPicker } from "@/components/create/BrandingPicker";
import { AiAssist } from "@/components/create/AiAssist";

/**
 * Create-event flow (TASK-0033) — the heart of Cue.
 *
 * A stack of progressive section cards, not a giant form: Basics → Classification
 * → Capacity → Branding, with an optional AI assist. Submitting validates the
 * draft, composes a `CueEvent`, persists it through the store, and returns to
 * the dashboard where it appears immediately.
 */
export function CreateEvent() {
  const navigate = useNavigate();
  const [draft, setDraft] = useState<EventDraft>(emptyDraft);
  const [errors, setErrors] = useState<DraftErrors>({});
  const [submitting, setSubmitting] = useState(false);

  function set<K extends keyof EventDraft>(key: K, value: EventDraft[K]) {
    setDraft((d) => ({ ...d, [key]: value }));
    if (errors[key]) setErrors((e) => ({ ...e, [key]: undefined }));
  }

  const assistInput = useMemo(
    () => ({
      title: draft.title,
      classification: draft.classification,
      customClassification: draft.customClassification,
      venue: draft.venue,
    }),
    [draft.title, draft.classification, draft.customClassification, draft.venue],
  );

  function submit(mode: "create" | "draft") {
    const found = validateDraft(draft, mode);
    setErrors(found);
    if (Object.values(found).some(Boolean)) {
      // Focus the first invalid field for keyboard/AT users.
      const firstKey = Object.keys(found)[0];
      document.getElementById(firstKey)?.focus();
      return;
    }
    setSubmitting(true);
    const event = draftToEvent(draft, {
      id: `evt-${crypto.randomUUID().slice(0, 8)}`,
      now: Date.now(),
      mode,
      host: currentUser.name,
    });
    createEvent(event);
    navigate(`/?created=${event.id}`);
  }

  return (
    <div className="animate-fade-up mx-auto max-w-3xl pb-28">
      {/* Heading */}
      <div className="mb-8">
        <Link
          to="/"
          className="focus-ring text-sm text-ink-muted transition-colors hover:text-ink"
        >
          ← Back to events
        </Link>
        <h1 className="mt-3 text-2xl font-semibold tracking-tight text-ink">
          Create event
        </h1>
        <p className="mt-1 text-sm text-ink-muted">
          A few essentials to spin up the workspace. You can refine everything later.
        </p>
      </div>

      <div className="space-y-5">
        {/* 1 — Basics */}
        <SectionCard
          step={1}
          title="Basics"
          description="Name it, place it in time and space."
          aside={
            <AiAssist input={assistInput} onUseDescription={(t) => set("summary", t)} />
          }
        >
          <div className="space-y-5">
            <div>
              <input
                id="title"
                value={draft.title}
                onChange={(e) => set("title", e.target.value)}
                placeholder="Event name"
                aria-label="Event name"
                className="focus-ring w-full rounded-lg bg-transparent text-2xl font-semibold text-ink placeholder:text-ink-faint/60"
              />
              {errors.title && (
                <p className="mt-1 text-xs text-red-400">{errors.title}</p>
              )}
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Date" htmlFor="date" error={errors.date}>
                <TextInput
                  id="date"
                  type="date"
                  value={draft.date}
                  invalid={!!errors.date}
                  onChange={(e) => set("date", e.target.value)}
                />
              </Field>
              <Field label="Time zone" htmlFor="timezone">
                <Select
                  id="timezone"
                  value={draft.timezone}
                  onChange={(e) => set("timezone", e.target.value)}
                >
                  {TIMEZONES.map((tz) => (
                    <option key={tz.value} value={tz.value}>
                      {tz.label}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Start time" htmlFor="startTime" error={errors.startTime}>
                <TextInput
                  id="startTime"
                  type="time"
                  value={draft.startTime}
                  invalid={!!errors.startTime}
                  onChange={(e) => set("startTime", e.target.value)}
                />
              </Field>
              <Field label="End time" htmlFor="endTime" error={errors.endTime}>
                <TextInput
                  id="endTime"
                  type="time"
                  value={draft.endTime}
                  invalid={!!errors.endTime}
                  onChange={(e) => set("endTime", e.target.value)}
                />
              </Field>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Venue" htmlFor="venue" optional>
                <TextInput
                  id="venue"
                  value={draft.venue}
                  onChange={(e) => set("venue", e.target.value)}
                  placeholder="e.g. Pier 27 Pavilion"
                />
              </Field>
              <Field label="Address" htmlFor="address" optional>
                <TextInput
                  id="address"
                  value={draft.address}
                  onChange={(e) => set("address", e.target.value)}
                  placeholder="Street, city"
                />
              </Field>
            </div>

            <Field label="Description" htmlFor="summary" optional>
              <TextArea
                id="summary"
                rows={3}
                value={draft.summary}
                onChange={(e) => set("summary", e.target.value)}
                placeholder="What is this event about? (or generate a draft with AI)"
              />
            </Field>
          </div>
        </SectionCard>

        {/* 2 — Classification */}
        <SectionCard
          step={2}
          title="Classification"
          description="What kind of event is this? Drives filtering and later analytics."
        >
          <ClassificationPicker
            value={draft.classification}
            custom={draft.customClassification}
            customError={errors.customClassification}
            onChange={(v) => set("classification", v)}
            onCustomChange={(v) => set("customClassification", v)}
          />
        </SectionCard>

        {/* 3 — Capacity */}
        <SectionCard
          step={3}
          title="Capacity"
          description="Set the room size and how guests join."
        >
          <div className="space-y-5">
            <Field
              label="Maximum attendees"
              htmlFor="maxAttendees"
              optional
              hint="Leave blank for no cap."
              error={errors.maxAttendees}
            >
              <TextInput
                id="maxAttendees"
                type="number"
                min={1}
                value={draft.maxAttendees}
                invalid={!!errors.maxAttendees}
                onChange={(e) => set("maxAttendees", e.target.value)}
                placeholder="Unlimited"
                className="max-w-40"
              />
            </Field>

            <ToggleRow
              label="RSVP enabled"
              hint="Guests confirm attendance."
              checked={draft.rsvpEnabled}
              onChange={(v) => set("rsvpEnabled", v)}
            />
            <ToggleRow
              label="Waitlist"
              hint={
                draft.rsvpEnabled
                  ? "Open a waitlist once capacity is reached."
                  : "Enable RSVP to use a waitlist."
              }
              checked={draft.waitlistEnabled}
              disabled={!draft.rsvpEnabled}
              onChange={(v) => set("waitlistEnabled", v)}
            />
          </div>
        </SectionCard>

        {/* 4 — Branding */}
        <SectionCard
          step={4}
          title="Branding"
          description="Give the event a look. Refine on the detail page later."
        >
          <BrandingPicker
            theme={draft.theme}
            title={draft.title}
            onThemeChange={(k) => set("theme", k)}
          />
        </SectionCard>
      </div>

      {/* Sticky action bar */}
      <div className="fixed inset-x-0 bottom-0 z-20 border-t border-line bg-canvas/85 backdrop-blur-xl">
        <div className="mx-auto flex max-w-3xl items-center justify-between gap-3 px-5 py-3.5 sm:px-8">
          <Link
            to="/"
            className="focus-ring rounded-xl px-3 py-2 text-sm font-medium text-ink-muted hover:text-ink"
          >
            Cancel
          </Link>
          <div className="flex items-center gap-2.5">
            <Button variant="outline" disabled={submitting} onClick={() => submit("draft")}>
              Save as draft
            </Button>
            <Button disabled={submitting} onClick={() => submit("create")}>
              Create event
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

function ToggleRow({
  label,
  hint,
  checked,
  onChange,
  disabled,
}: {
  label: string;
  hint: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-4 rounded-xl border border-line bg-canvas px-4 py-3">
      <div>
        <p className="text-sm font-medium text-ink">{label}</p>
        <p className="text-xs text-ink-faint">{hint}</p>
      </div>
      <Switch label={label} checked={checked} onChange={onChange} disabled={disabled} />
    </div>
  );
}
