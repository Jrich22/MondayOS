import { useState } from "react";
import type { CueEvent, Guest } from "@/lib/types";
import {
  useInvitation,
  issueInvitation,
  setAllowance,
  rotateInvitation,
  revokeInvitation,
  markDelivered,
} from "@/lib/invitation-store";
import { rsvpToken } from "@/lib/invitation";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/cn";

/**
 * Organizer invitation controls for a single existing guest (Invite & RSVP
 * slice 1). Renders inside the GuestDrawer for saved guests. The Invitation is
 * the canonical owner of token/lifecycle state; RSVP status stays on the Guest
 * record (shown in the Attendance section), so this adds only the minimum
 * invitation-state visibility needed to run and read the loop.
 */
export function InvitationSection({ guest, event }: { guest: Guest; event: CueEvent }) {
  const inv = useInvitation(guest.id);
  const [copied, setCopied] = useState(false);

  if (!inv) {
    return (
      <div className="space-y-2">
        <p className="text-sm text-ink-muted">No invitation issued yet.</p>
        <Button variant="outline" onClick={() => issueInvitation(event.id, guest.id)}>Issue invitation</Button>
      </div>
    );
  }

  const token = rsvpToken(inv);
  const link = `${window.location.origin}/#/rsvp/${encodeURIComponent(token)}`;
  const revoked = inv.status === "revoked";

  async function copy() {
    try {
      await navigator.clipboard?.writeText(link);
    } catch {
      /* clipboard blocked — the link is still visible to copy manually */
    }
    markDelivered(guest.id); // simulated delivery updates canonical state
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
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

      {inv.delivered && <p className="text-xs text-ink-muted">Simulated delivery sent{inv.deliveredAt ? ` · ${new Date(inv.deliveredAt).toLocaleString()}` : ""}.</p>}
      {inv.respondedAt && <p className="text-xs text-emerald-300">Guest responded · {new Date(inv.respondedAt).toLocaleString()} (RSVP shown in Attendance above).</p>}

      <div>
        <label htmlFor="inv-allow" className="text-sm font-medium text-ink">Plus-one allowance</label>
        <input
          id="inv-allow" type="number" min={0} inputMode="numeric"
          value={inv.plusOneAllowance}
          onChange={(e) => setAllowance(guest.id, Math.max(0, Number(e.target.value) || 0))}
          className="focus-ring ml-2 w-20 rounded-xl border border-line bg-transparent px-3 py-1.5 text-sm text-ink"
        />
        <span className="ml-2 text-xs text-ink-muted">default 0 — grant explicitly</span>
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
        {!revoked && <a href={link} target="_blank" rel="noreferrer"
          className="focus-ring inline-flex items-center justify-center rounded-xl border border-line-strong px-3.5 py-2 text-sm font-medium text-ink hover:bg-white/5">
          Open guest preview
        </a>}
        <Button variant="outline" onClick={() => rotateInvitation(guest.id)}>{revoked ? "Re-issue (new link)" : "Rotate token"}</Button>
        {!revoked && <Button variant="ghost" onClick={() => revokeInvitation(guest.id)}>Revoke</Button>}
      </div>

      <p className="text-[11px] text-ink-muted">Rotating or revoking invalidates all prior links immediately. Tokens carry no PII and are not production-secure.</p>
    </div>
  );
}
