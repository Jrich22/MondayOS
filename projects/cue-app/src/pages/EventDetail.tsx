import { useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { useEvent } from "@/lib/store";
import { formatEventDate, formatTimeRange } from "@/lib/format";
import { classificationLabel } from "@/lib/classification";
import { StatusBadge } from "@/components/dashboard/StatusBadge";
import { Button } from "@/components/ui/Button";
import { AiPanel } from "@/components/detail/AiPanel";
import { OverviewTab } from "@/components/detail/tabs/OverviewTab";
import { RollCallTab } from "@/components/detail/tabs/RollCallTab";
import { AgendaTab } from "@/components/detail/tabs/AgendaTab";
import { GuestsTab } from "@/components/detail/tabs/GuestsTab";
import { AssistantTab } from "@/components/detail/tabs/AssistantTab";
import { PlaceholderTab } from "@/components/detail/tabs/PlaceholderTab";
import { cn } from "@/lib/cn";
import {
  LayoutIcon,
  UsersIcon,
  CheckCircleIcon,
  ListIcon,
  BuildingIcon,
  DocumentIcon,
  MailIcon,
  SparklesIcon,
  ChartIcon,
  EditIcon,
  ShareIcon,
  MoreIcon,
} from "@/components/icons";

type TabKey =
  | "overview"
  | "guests"
  | "rollcall"
  | "agenda"
  | "vendors"
  | "documents"
  | "communications"
  | "assistant"
  | "analytics";

const TABS: { key: TabKey; label: string; icon: ReactNode }[] = [
  { key: "overview", label: "Overview", icon: <LayoutIcon /> },
  { key: "guests", label: "Guests", icon: <UsersIcon /> },
  { key: "rollcall", label: "Roll Call", icon: <CheckCircleIcon /> },
  { key: "agenda", label: "Agenda", icon: <ListIcon /> },
  { key: "vendors", label: "Vendors", icon: <BuildingIcon /> },
  { key: "documents", label: "Documents", icon: <DocumentIcon /> },
  { key: "communications", label: "Communications", icon: <MailIcon /> },
  { key: "assistant", label: "AI Assistant", icon: <SparklesIcon /> },
  { key: "analytics", label: "Analytics", icon: <ChartIcon /> },
];

/**
 * Event Detail — the operational command center for a single event (TASK-0034).
 *
 * A header (identity + quick actions), a section tab rail, a main content area,
 * and a persistent AI assistant rail. Built for daily operations: Overview,
 * Roll Call, and Agenda are live surfaces; the remaining sections are shelled in
 * against the event.id container contract (DEC-0006) so later tasks slot in.
 */
export function EventDetail() {
  const { id } = useParams();
  const event = useEvent(id);
  const [tab, setTab] = useState<TabKey>("overview");

  if (!event) {
    return (
      <div className="animate-fade-up flex min-h-[60vh] flex-col items-center justify-center text-center">
        <h1 className="text-xl font-semibold text-ink">Event not found</h1>
        <p className="mt-2 text-sm text-ink-muted">
          This event doesn't exist or was removed.
        </p>
        <Link to="/" className="mt-4">
          <Button variant="outline">Back to events</Button>
        </Link>
      </div>
    );
  }

  return (
    <div className="animate-fade-up">
      {/* Header */}
      <header className="mb-6">
        <Link to="/" className="focus-ring text-sm text-ink-muted hover:text-ink">
          ← Events
        </Link>
        <div className="mt-3 flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-3">
              <h1 className="truncate text-2xl font-semibold tracking-tight text-ink">
                {event.title}
              </h1>
              <StatusBadge status={event.status} />
            </div>
            <p className="mt-1.5 text-sm text-ink-muted">
              {formatEventDate(event.startsAt)} · {formatTimeRange(event.startsAt, event.endsAt)}
              {"  ·  "}
              {classificationLabel(event.classification, event.customClassification)}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="ghost" aria-label="Share">
              <ShareIcon width={18} height={18} />
            </Button>
            <Button variant="ghost" aria-label="More actions">
              <MoreIcon width={18} height={18} />
            </Button>
            <Button variant="outline">
              <EditIcon width={16} height={16} />
              Edit
            </Button>
          </div>
        </div>
      </header>

      {/* Body: tab rail · content · AI rail */}
      <div className="flex flex-col gap-6 xl:flex-row">
        {/* Section navigation */}
        <nav className="xl:w-52 xl:shrink-0">
          <div className="flex gap-1 overflow-x-auto pb-1 xl:flex-col xl:overflow-visible xl:pb-0">
            {TABS.map((t) => {
              const active = t.key === tab;
              return (
                <button
                  key={t.key}
                  onClick={() => setTab(t.key)}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "focus-ring flex shrink-0 items-center gap-2.5 rounded-xl px-3 py-2 text-sm font-medium transition-colors",
                    active
                      ? "bg-white/[0.06] text-ink"
                      : "text-ink-muted hover:bg-white/[0.03] hover:text-ink",
                  )}
                >
                  <span className={cn(active ? "text-brand-400" : "text-ink-faint")}>
                    {t.icon}
                  </span>
                  {t.label}
                </button>
              );
            })}
          </div>
        </nav>

        {/* Main content */}
        <div className="min-w-0 flex-1">
          {tab === "overview" && <OverviewTab event={event} />}
          {tab === "guests" && <GuestsTab event={event} />}
          {tab === "rollcall" && <RollCallTab event={event} />}
          {tab === "agenda" && <AgendaTab event={event} />}
          {tab === "assistant" && <AssistantTab event={event} />}
          {tab === "vendors" && (
            <PlaceholderTab
              title="Vendors & venue"
              task="TASK-0024"
              icon={<BuildingIcon />}
              blurb="Track venues, caterers, AV, and other suppliers — contacts, status, and cost — in one place per event."
              bullets={["Vendor list", "Status", "Contacts", "Costs"]}
            />
          )}
          {tab === "documents" && (
            <PlaceholderTab
              title="Documents"
              task="Later sprint"
              icon={<DocumentIcon />}
              blurb="Attach contracts, run sheets, floor plans, and briefs so the whole team works from the same files."
              bullets={["Contracts", "Run sheets", "Floor plans"]}
            />
          )}
          {tab === "communications" && (
            <PlaceholderTab
              title="Communications"
              task="TASK-0023"
              icon={<MailIcon />}
              blurb="Send invites, reminders, and follow-ups, and see what went to whom — the in-app invite & RSVP flow."
              bullets={["Invites", "Reminders", "Follow-ups"]}
            />
          )}
          {tab === "analytics" && (
            <PlaceholderTab
              title="Analytics"
              task="TASK-0027"
              icon={<ChartIcon />}
              blurb="Post-event insights: attendance, engagement, portfolio reach, and what to do differently next time."
              bullets={["Attendance", "Engagement", "Portfolio reach"]}
            />
          )}
        </div>

        {/* Persistent AI assistant */}
        <aside className="xl:w-80 xl:shrink-0">
          <div className="card p-5 xl:sticky xl:top-20">
            <AiPanel event={event} />
          </div>
        </aside>
      </div>
    </div>
  );
}
