import { useState } from "react";
import type { CueEvent, Guest } from "@/lib/types";
import {
  useInvitation,
  issueInvitation,
  setAllowance,
  rotateInvitation,
  revokeInvitation,
  recordSimulatedDelivery,
} from "@/lib/invitation-store";
import { rsvpToken } from "@/lib/invitation";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/cn";

/**
 * Organizer invitation controls for a single existing guest (Invite & RSVP
 * slice 1). Renders inside the GuestDrawer for saved guests. The Invitation is
 * canonical for token/lifecycle state; the Guest record is canonical for RSVP.
 *
 * Correctness rules enforced here (review round):
 *   • Delivery is only recorded on a genuine success (clipboard OR explicit mark),
 *     and it updates both records via one store op — never on a failed copy.
 *   • Allowance changes are rejected (with an explanation) below the accepted
 *     plus-one count; the invariant lives in domain/store logic.
 *   • Issuance is blocked when RSVP is disabled for the event.
 */
export function InvitationSection({ guest, event }: { guest: Guest; event: CueEvent }) {
  const inv = useInvitation(guest.id);
  const [copied, setCopied] = useState(false);
  const [notice, setNotice] = useState<{ tone: "warn" | "error"; msg: string } | null>(null);

  if (!event.capacity.rsvpEnabled) {
    return (
      <p className="text-sm text-amber-300">
        RSVP is turned off for this event. Enable RSVP in the event's capacity settings to issue invitations.
      </p>
    );
  }

  if (!inv) {
    return (
      <div className="space-y-2">
        <p className="text-sm text-ink-muted">No invitation issued yet.</p>
        <Button variant="outline" onClick={() => issueInvitation(event.id, guest.id)}>Issue invitation</Button>
      </div>
    );
  }

  const token = rsvpToken(inv);
  const link = `${window.location.origin}/#/rsvp/${token}`;
  const revoked = inv.status === "revoked";

  async function copy() {
    try {
      if (!navigator.clipboard?.writeText) throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText(link);
    } catch {
      // Honest failure — do NOT show "Copied" or mark delivered.
      setCopied(false);
      setNotice({ tone: "warn", msg: "Couldn't copy automatically. Select the link above and copy it manually, or use “Mark delivered”." });
      return;
    }
    recordSimulatedDelivery(guest.id); // both records, only on real success
    setNotice(null);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  function changeAllowance(next: number) {
    const res = setAllowance(guest.id, next);
    if (!res.ok) setNotice({ tone: "error", msg: res.reason });
    else setNotice(null);
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className={cn("rounded-full border px-2 py-0.5 text-xs font-medium",
          revoked ? "border-rose-500/40 text-rose-300" : "border-emerald-500/40 text-emerald-300")}>
          {revoked ? "Revoked" : "Active"}
        </span>
        <span className="text-ink-muted">Token v{inv.tokenVersion} · issued {new Date(inv.issuedAt).toLocaleString()}</span>
      </div>

      {inv.delivered
        ? <p className="text-xs text-ink-muted">Simulated delivery sent{inv.deliveredAt ? ` · ${new Date(inv.deliveredAt).toLocaleString()}` : ""}.</p>
        : inv.rotationCount > 0
          ? <p className="text-xs text-amber-300">Reissued (rotation #{inv.rotationCount}) — the new link has not been delivered yet.</p>
          : <p className="text-xs text-ink-muted">Not delivered yet.</p>}
      {inv.respondedAt && <p className="text-xs text-emerald-300">Guest responded · {new Date(inv.respondedAt).toLocaleString()} (RSVP shown in Attendance above).</p>}

      {notice && (
        <p role="alert" className={cn("rounded-lg border px-3 py-1.5 text-xs",
          notice.tone === "error" ? "border-rose-500/40 text-rose-300" : "border-amber-500/40 text-amber-300")}>
          {notice.msg}
        </p>
      )}

      <div>
        <label htmlFor="inv-allow" className="text-sm font-medium text-ink">Plus-one allowance</label>
        <input
          id="inv-allow" type="number" min={0} inputMode="numeric" defaultValue={inv.plusOneAllowance}
          key={inv.plusOneAllowance}
          onChange={(e) => changeAllowance(Math.max(0, Number(e.target.value) || 0))}
          className="focus-ring ml-2 w-20 rounded-xl border border-line bg-transparent px-3 py-1.5 text-sm text-ink"
        />
        <span className="ml-2 text-xs text-ink-muted">default 0 · can't drop below accepted ({guest.attendance.plusOnes})</span>
      </div>

      {!revoked && (
        <div>
          <label htmlFor="inv-link" className="text-sm font-medium text-ink">Response link (prototype, same-browser)</label>
          <input id="inv-link" readOnly value={link} aria-label="Prototype response link"
            className="focus-ring mt-1 w-full truncate rounded-xl border border-line bg-transparent px-3 py-1.5 text-xs text-ink-muted" />
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        {!revoked && <Button variant="outline" onClick={copy}>{copied ? "Copied ✓" : "Copy link"}</Button>}
        {!revoked && !inv.delivered && <Button variant="outline" onClick={() => { recordSimulatedDelivery(guest.id); setNotice(null); }}>Mark delivered</Button>}
        {!revoked && <a href={link} target="_blank" rel="noreferrer"
          className="focus-ring inline-flex items-center justify-center rounded-xl border border-line-strong px-3.5 py-2 text-sm font-medium text-ink hover:bg-white/5">
          Open guest preview
        </a>}
        <Button variant="outline" onClick={() => { rotateInvitation(guest.id); setNotice(null); }}>{revoked ? "Re-issue (new link)" : "Rotate token"}</Button>
        {!revoked && <Button variant="ghost" onClick={() => revokeInvitation(guest.id)}>Revoke</Button>}
      </div>

      <p className="text-[11px] text-ink-muted">Rotating or revoking invalidates all prior links immediately and (on rotation) resets delivery. Tokens carry no PII and are not production-secure.</p>
    </div>
  );
}
