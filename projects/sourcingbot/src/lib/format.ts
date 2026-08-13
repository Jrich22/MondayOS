/**
 * Display formatting. Presentation only — no domain logic lives here.
 */

/**
 * Human relative time: "2h ago", "3d ago", "just now".
 *
 * Coarse on purpose. A recruiter scanning a dashboard needs to know whether
 * something moved today or last month; "14 minutes ago" is precision nobody
 * acts on, and it makes every row look urgent.
 */
export function relativeTime(iso: string, now: Date = new Date()): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";

  const seconds = Math.round((now.getTime() - then) / 1000);
  if (seconds < 0) return "just now";
  if (seconds < 60) return "just now";

  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;

  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;

  const days = Math.round(hours / 24);
  if (days < 7) return `${days}d ago`;
  if (days < 30) return `${Math.round(days / 7)}w ago`;
  if (days < 365) return `${Math.round(days / 30)}mo ago`;
  return `${Math.round(days / 365)}y ago`;
}
