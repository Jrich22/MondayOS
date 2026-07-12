import { useState } from "react";
import type { GuestRole } from "@/lib/types";
import { ALL_ROLES, ROLE_META } from "@/lib/guests-select";
import { canCreateWalkIn, type WalkInInput } from "@/lib/checkin";
import { Field, TextInput } from "@/components/ui/Field";
import { Switch } from "@/components/ui/Switch";
import { Button } from "@/components/ui/Button";
import { StarIcon, UserPlusIcon } from "@/components/icons";
import { cn } from "@/lib/cn";

/**
 * Walk-In Mode — capture someone at the door who isn't on the list. A short form
 * (only a name is required), then Create-and-check-in: the parent builds the
 * attendee through `walkInGuest` (which assigns the stable QR identity and marks
 * them checked in), adds them to the store, shows the badge, and returns here to
 * the scanner. Kept intentionally minimal — the door is not the place for the
 * full CRM editor; the record can be enriched later in Guest Management.
 */
export function WalkInPanel({ onCreate }: { onCreate: (input: WalkInInput) => void }) {
  const [input, setInput] = useState<WalkInInput>({ firstName: "", lastName: "" });

  const set = (patch: Partial<WalkInInput>) => setInput((i) => ({ ...i, ...patch }));
  const toggleRole = (role: GuestRole) =>
    setInput((i) => ({
      ...i,
      roles: i.roles?.includes(role)
        ? i.roles.filter((r) => r !== role)
        : [...(i.roles ?? []), role],
    }));

  const ready = canCreateWalkIn(input);

  const submit = () => {
    if (!ready) return;
    onCreate(input);
    setInput({ firstName: "", lastName: "" });
  };

  return (
    <div className="mx-auto max-w-lg space-y-5">
      <div className="flex items-center gap-2.5">
        <span className="grid h-9 w-9 place-items-center rounded-xl bg-brand-500/15 text-brand-300">
          <UserPlusIcon width={18} height={18} />
        </span>
        <div>
          <h3 className="text-sm font-semibold text-ink">New walk-in</h3>
          <p className="text-xs text-ink-faint">Create, badge, and check in — then back to the scanner.</p>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <Field label="First name" htmlFor="w-first">
          <TextInput
            id="w-first"
            autoFocus
            value={input.firstName}
            onChange={(e) => set({ firstName: e.target.value })}
            onKeyDown={(e) => e.key === "Enter" && submit()}
            placeholder="Ava"
          />
        </Field>
        <Field label="Last name" htmlFor="w-last">
          <TextInput
            id="w-last"
            value={input.lastName}
            onChange={(e) => set({ lastName: e.target.value })}
            onKeyDown={(e) => e.key === "Enter" && submit()}
            placeholder="Chen"
          />
        </Field>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <Field label="Company" htmlFor="w-co" optional>
          <TextInput
            id="w-co"
            value={input.company ?? ""}
            onChange={(e) => set({ company: e.target.value })}
            placeholder="Company"
          />
        </Field>
        <Field label="Job title" htmlFor="w-title" optional>
          <TextInput
            id="w-title"
            value={input.jobTitle ?? ""}
            onChange={(e) => set({ jobTitle: e.target.value })}
            placeholder="Title"
          />
        </Field>
      </div>

      <Field label="Roles" optional>
        <div className="flex flex-wrap gap-1.5">
          {ALL_ROLES.map((role) => {
            const on = input.roles?.includes(role) ?? false;
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

      <div className="flex items-center justify-between gap-3 rounded-xl border border-line px-3 py-2.5">
        <div className="flex items-center gap-2">
          <StarIcon width={16} height={16} className={input.vip ? "text-status-draft" : "text-ink-muted"} />
          <div>
            <p className="text-sm font-medium text-ink">VIP</p>
            <p className="text-xs text-ink-faint">Triggers the arrival celebration on check-in</p>
          </div>
        </div>
        <Switch checked={input.vip ?? false} onChange={(v) => set({ vip: v })} label="VIP" />
      </div>

      <Button onClick={submit} disabled={!ready} className={cn("w-full py-3 text-base", !ready && "opacity-50")}>
        <UserPlusIcon width={18} height={18} />
        Create &amp; check in
      </Button>
    </div>
  );
}
