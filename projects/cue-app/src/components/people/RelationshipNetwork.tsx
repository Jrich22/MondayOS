import { Link } from "react-router-dom";
import type { PersonNetwork } from "@/lib/person-graph";
import { PersonAvatar } from "./PersonAvatar";
import { BuildingIcon, NetworkIcon, ArrowRightIcon } from "@/components/icons";

/**
 * The relationship neighborhood — the graph, rendered. Who this person is
 * frequently seen with (weighted by shared events), the companies orbiting their
 * network, and the organizations they share. All derived from co-attendance in
 * lib/person-graph; every connection links onward to that person's profile.
 */
export function RelationshipNetwork({ network }: { network: PersonNetwork }) {
  const { coAttendees, companies, organizations } = network;

  return (
    <section className="card p-5">
      <div className="flex items-center gap-2">
        <NetworkIcon width={16} height={16} className="text-brand-400" />
        <h3 className="text-sm font-semibold text-ink">Relationship network</h3>
      </div>

      {coAttendees.length === 0 ? (
        <p className="mt-4 text-sm text-ink-muted">
          No shared events yet — this person hasn't crossed paths with others in the network.
        </p>
      ) : (
        <>
          <p className="mt-3 text-[11px] font-semibold uppercase tracking-wide text-ink-faint">
            Frequently seen with
          </p>
          <ul className="mt-2 space-y-1">
            {coAttendees.map((tie) => (
              <li key={tie.person.id}>
                <Link
                  to={`/people/${tie.person.id}`}
                  className="focus-ring group flex items-center gap-3 rounded-xl px-2 py-2 transition-colors hover:bg-white/[0.03]"
                >
                  <PersonAvatar person={tie.person} size="sm" />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-ink">{tie.person.displayName}</p>
                    <p className="truncate text-[11px] text-ink-faint">
                      {tie.person.company || "—"}
                    </p>
                  </div>
                  <span className="shrink-0 rounded-full bg-white/[0.05] px-2 py-0.5 text-[11px] font-medium text-ink-muted">
                    {tie.count} shared
                  </span>
                  <ArrowRightIcon
                    width={14}
                    height={14}
                    className="shrink-0 text-ink-faint opacity-0 transition-opacity group-hover:opacity-100"
                  />
                </Link>
              </li>
            ))}
          </ul>
        </>
      )}

      {companies.length > 0 && (
        <div className="mt-5 border-t border-line pt-4">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-faint">
            Companies represented
          </p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {companies.map((c) => (
              <span
                key={c.name}
                className="inline-flex items-center gap-1.5 rounded-full border border-line px-2.5 py-0.5 text-xs font-medium text-ink-muted"
              >
                <BuildingIcon width={12} height={12} className="text-ink-faint" />
                {c.name}
                {c.count > 1 && <span className="text-ink-faint">×{c.count}</span>}
              </span>
            ))}
          </div>
        </div>
      )}

      {organizations.length > 0 && (
        <div className="mt-5 border-t border-line pt-4">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-faint">
            Shared organizations
          </p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {organizations.map((o) => (
              <span
                key={o}
                className="rounded-full border border-brand-500/25 bg-brand-500/10 px-2.5 py-0.5 text-xs font-medium text-brand-200"
              >
                {o}
              </span>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
