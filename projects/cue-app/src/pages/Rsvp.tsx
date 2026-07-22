import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import type { CueEvent, Guest } from "@/lib/types";
import { useEvent } from "@/lib/store";
import { useGuests, updateGuest } from "@/lib/guests";
import { useInvitation, markResponded } from "@/lib/invitation-store";
import {
  parseRsvpToken,
  resolveRsvp,
  decideResponse,
  clampPlusOnes,
  type Invitation,
  type RsvpChoice,
} from "@/lib/invitation";
import { formatEventDate } from "@/lib/format";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/cn";

/**
 * Guest response surface — a same-browser PROTOTYPE preview (DEC-0011 §1).
 * It reads the CANONICAL event/guest/invitation from the operator's browser
 * store, resolves the opaque token to a clear state, and — when open — lets the
 * guest respond, updating the canonical Guest (RSVP/plus-ones/preferences) and
 * capacity/waitlist state. It is NOT production-public or cross-device, and it
 * must not encourage real sensitive input.
 *
 * Routed full-viewport (outside the app shell) like Roll Call / Check-In. The
 * outer component resolves the token; the inner ResponseForm is module-level so
 * its local state survives canonical-store re-renders.
 */
export default function Rsvp() {
  const { token } = useParams<{ token: string }>();
  const parsed = token ? parseRsvpToken(token) : null;
  const eventId = parsed?.eventId;
  const guestId = parsed?.guestId;

  const event = useEvent(eventId);
  const guests = useGuests(eventId ?? "");
  const invitation = useInvitation(guestId);
  const guest = guestId ? guests.find((g) => g.id === guestId) : undefined;
  const now = Date.now();

  const res = useMemo(
    () => resolveRsvp(token ?? "", { event, guest, invitation, now }),
    [token, event, guest, invitation, now],
  );

  return (
    <div className="min-h-screen bg-canvas text-ink">
      <PrototypeBanner />
      <main className="mx-auto max-w-md px-4 py-6">
        {res.status === "ok" && event && guest && invitation ? (
          <ResponseForm event={event} guest={guest} invitation={invitation} allGuests={guests} />
        ) : (
          <ClosedState status={res.status} reason={res.reason} eventTitle={event?.title} />
        )}
      </main>
    </div>
  );
}

const STATE_UI: Record<string, { title: string; tone: "neutral" | "warn" | "error" }> = {
  invalid: { title: "This response link isn't valid", tone: "error" },
  revoked: { title: "This invitation was revoked", tone: "error" },
  rotated: { title: "This link has been replaced", tone: "warn" },
  "wrong-event": { title: "This link is for a different event", tone: "error" },
  expired: { title: "This event has ended", tone: "neutral" },
  "event-started": { title: "Responses are closed", tone: "warn" },
};

function ResponseForm({
  event: e,
  guest: g,
  invitation: inv,
  allGuests,
}: {
  event: CueEvent;
  guest: Guest;
  invitation: Invitation;
  allGuests: Guest[];
}) {
  const [choice, setChoice] = useState<RsvpChoice>(
    g.attendance.rsvp === "tentative" || g.attendance.rsvp === "declined" ? g.attendance.rsvp
      : g.attendance.rsvp === "confirmed" ? "confirmed" : "confirmed",
  );
  const [plusOnes, setPlusOnes] = useState<number>(clampPlusOnes(g.attendance.plusOnes, inv.plusOneAllowance));
  const [dietary, setDietary] = useState(g.preferences.dietary ?? "");
  const [accessibility, setAccessibility] = useState(g.preferences.accessibility ?? "");
  const [banner, setBanner] = useState<{ tone: "ok" | "warn" | "error"; msg: string } | null>(null);

  const otherGuests = allGuests.filter((x) => x.id !== g.id);

  function submit() {
    const outcome = decideResponse(e, otherGuests, inv, { choice, plusOnes });
    if (outcome.kind === "blocked") {
      setBanner({ tone: "error", msg: outcome.reason });
      return;
    }
    updateGuest({
      ...g,
      attendance: { ...g.attendance, rsvp: outcome.rsvp, plusOnes: outcome.plusOnes, waitlisted: outcome.waitlisted },
      preferences: { ...g.preferences, dietary: dietary.trim() || undefined, accessibility: accessibility.trim() || undefined },
    });
    markResponded(g.id);
    setBanner({ tone: outcome.waitlisted ? "warn" : "ok", msg: outcome.message });
  }

  function withdraw() {
    updateGuest({ ...g, attendance: { ...g.attendance, rsvp: "invited", plusOnes: 0, waitlisted: false } });
    setBanner({ tone: "warn", msg: "Your response was withdrawn. You can respond again anytime before the event." });
  }

  return (
    <section aria-labelledby="rsvp-h" className="space-y-5">
      <header className="rounded-2xl border border-line bg-white/5 p-5">
        <p className="text-xs uppercase tracking-wide text-ink-muted">You're invited</p>
        <h1 id="rsvp-h" className="mt-1 text-xl font-semibold text-ink">{e.title}</h1>
        <p className="mt-1 text-sm text-ink-muted">{formatEventDate(e.startsAt)}</p>
        <p className="text-sm text-ink-muted">{[e.venue, e.city].filter(Boolean).join(" · ")}</p>
        {e.summary && <p className="mt-3 text-sm text-ink-muted">{e.summary}</p>}
      </header>

      {banner && (
        <p role="status" aria-live="polite"
           className={cn("rounded-xl border px-3 py-2 text-sm",
             banner.tone === "ok" ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
             : banner.tone === "warn" ? "border-amber-500/40 bg-amber-500/10 text-amber-300"
             : "border-rose-500/40 bg-rose-500/10 text-rose-300")}>
          {banner.msg}
        </p>
      )}

      <fieldset className="space-y-2">
        <legend className="text-sm font-medium text-ink">Will you attend?</legend>
        {(["confirmed", "tentative", "declined"] as RsvpChoice[]).map((c) => (
          <label key={c} className="flex items-center gap-3 rounded-xl border border-line px-3 py-2 text-sm">
            <input type="radio" name="rsvp" value={c} checked={choice === c} onChange={() => setChoice(c)} className="focus-ring" />
            <span className="text-ink">{c === "confirmed" ? "Yes, I'll be there" : c === "tentative" ? "Tentative" : "Can't make it"}</span>
          </label>
        ))}
      </fieldset>

      {choice !== "declined" && inv.plusOneAllowance > 0 && (
        <div>
          <label htmlFor="plus-ones" className="text-sm font-medium text-ink">
            Plus-ones <span className="text-ink-muted">(up to {inv.plusOneAllowance})</span>
          </label>
          <input
            id="plus-ones" type="number" min={0} max={inv.plusOneAllowance} inputMode="numeric"
            value={plusOnes}
            onChange={(ev) => setPlusOnes(clampPlusOnes(Number(ev.target.value), inv.plusOneAllowance))}
            className="focus-ring mt-1 w-24 rounded-xl border border-line bg-transparent px-3 py-2 text-sm text-ink"
          />
        </div>
      )}

      {choice !== "declined" && (
        <div className="space-y-3">
          <p className="text-xs text-ink-muted">Optional — <strong>demo/synthetic data only</strong> in this prototype. Do not enter real sensitive information.</p>
          <div>
            <label htmlFor="dietary" className="text-sm font-medium text-ink">Dietary needs</label>
            <input id="dietary" value={dietary} onChange={(e2) => setDietary(e2.target.value)} placeholder="e.g. vegetarian (demo)"
              className="focus-ring mt-1 w-full rounded-xl border border-line bg-transparent px-3 py-2 text-sm text-ink placeholder:text-ink-muted" />
          </div>
          <div>
            <label htmlFor="a11y" className="text-sm font-medium text-ink">Accessibility needs</label>
            <input id="a11y" value={accessibility} onChange={(e2) => setAccessibility(e2.target.value)} placeholder="e.g. step-free access (demo)"
              className="focus-ring mt-1 w-full rounded-xl border border-line bg-transparent px-3 py-2 text-sm text-ink placeholder:text-ink-muted" />
          </div>
        </div>
      )}

      <div className="flex flex-wrap gap-2 pb-8">
        <Button onClick={submit}>Send response</Button>
        <Button variant="outline" onClick={withdraw}>Withdraw</Button>
      </div>
    </section>
  );
}

function PrototypeBanner() {
  return (
    <div role="note" className="border-b border-amber-500/30 bg-amber-500/10 px-4 py-2 text-center text-xs text-amber-200">
      Prototype preview · same-browser only · not a public or production link · use demo data only
    </div>
  );
}

function ClosedState({ status, reason, eventTitle }: { status: string; reason: string; eventTitle?: string }) {
  const ui = STATE_UI[status] ?? { title: "Response link", tone: "neutral" as const };
  const tone = ui.tone;
  return (
    <section className="mt-6 rounded-2xl border border-line bg-white/5 p-6 text-center" aria-labelledby="closed-h">
      <h1 id="closed-h" className={cn("text-lg font-semibold",
        tone === "error" ? "text-rose-300" : tone === "warn" ? "text-amber-300" : "text-ink")}>
        {ui.title}
      </h1>
      {eventTitle && <p className="mt-1 text-sm text-ink-muted">{eventTitle}</p>}
      <p role="status" className="mt-3 text-sm text-ink-muted">{reason}</p>
    </section>
  );
}
