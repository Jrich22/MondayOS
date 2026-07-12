import { useEffect, useMemo, useState } from "react";
import { useEvents } from "@/lib/store";
import { useGuests } from "@/lib/guests";
import {
  useCampaigns,
  addCampaign,
  updateCampaign,
  removeCampaign,
} from "@/lib/comms-store";
import {
  campaignsForEvent,
  campaignsForStage,
  stageCounts,
  audienceCount,
  projectMetrics,
  newCampaign,
  STAGE_META,
  type Campaign,
  type CampaignStage,
} from "@/lib/comms";
import type { CommsTemplate } from "@/lib/comms-data";
import { formatEventDate } from "@/lib/format";
import { StageRail, type CommsView } from "@/components/comms/StageRail";
import { MetricsBar } from "@/components/comms/MetricsBar";
import { CampaignList } from "@/components/comms/CampaignList";
import { CampaignBuilder } from "@/components/comms/CampaignBuilder";
import { TemplateGallery } from "@/components/comms/TemplateGallery";
import { RightPanel, type RightTab } from "@/components/comms/RightPanel";
import { Select } from "@/components/ui/Field";
import { CommsIcon } from "@/components/comms/commsIcons";
import { MailIcon, PlusIcon } from "@/components/icons";
import { cn } from "@/lib/cn";

/**
 * Communications Center (TASK-0043) — Cue's outbound-comms mission control,
 * spanning the entire event lifecycle from Save the Date to the post-event
 * Survey. A three-pane workspace: lifecycle stages on the left, the Campaign
 * Builder in the center, and a tabbed context panel on the right (Preview, AI
 * Assistant, Audience, Schedule, History).
 *
 * It is pure composition over the shared stores — events (lib/store), the guest
 * roster (lib/guests), and campaigns (lib/comms-store) — with every operational
 * rule living in lib/comms, lib/comms-ai, and lib/comms-history. Campaigns tie to
 * an event by id and resolve their audience live from the roster, so the
 * workspace stays in lockstep with Guest Management, the Event Lifecycle, and
 * Mission Control. No backend, no email provider, no real AI — all local/mock.
 */

let idCounter = 0;
function newId(prefix: string): string {
  idCounter += 1;
  return `${prefix}-${Date.now().toString(36)}-${idCounter}`;
}

/** Substitute event-level tokens when applying a template (first_name stays). */
function mergeEventTokens(text: string, title: string, venue: string, host: string, date: string): string {
  return text
    .replaceAll("{{event_name}}", title)
    .replaceAll("{{venue}}", venue || "the venue")
    .replaceAll("{{host}}", host)
    .replaceAll("{{event_date}}", date);
}

export function Communications() {
  const events = useEvents();
  const allCampaigns = useCampaigns();

  // Default to the flagship event so the workspace opens on rich, walkable data.
  const defaultEventId = useMemo(
    () => events.find((e) => e.tags.includes("flagship"))?.id ?? events[0]?.id ?? "",
    [events],
  );
  const [eventId, setEventId] = useState(defaultEventId);
  const activeEvent = events.find((e) => e.id === eventId) ?? events.find((e) => e.id === defaultEventId);
  const effectiveEventId = activeEvent?.id ?? "";

  const guests = useGuests(effectiveEventId);

  const [view, setView] = useState<CommsView>({ type: "stage", stage: "invitations" });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [rightTab, setRightTab] = useState<RightTab>("preview");
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 15000);
    return () => clearInterval(t);
  }, []);

  const eventCampaigns = useMemo(
    () => campaignsForEvent(allCampaigns, effectiveEventId),
    [allCampaigns, effectiveEventId],
  );
  const counts = useMemo(
    () => stageCounts(allCampaigns, effectiveEventId),
    [allCampaigns, effectiveEventId],
  );

  const stage = view.type === "stage" ? view.stage : null;
  const stageCampaigns = useMemo(
    () => (stage ? campaignsForStage(allCampaigns, effectiveEventId, stage) : []),
    [allCampaigns, effectiveEventId, stage],
  );

  // Keep the selection valid: when the stage/event changes, fall back to the
  // first campaign in the stage (or none) so the builder always has a target.
  useEffect(() => {
    if (view.type !== "stage") return;
    const inStage = stageCampaigns.some((c) => c.id === selectedId);
    if (!inStage) setSelectedId(stageCampaigns[0]?.id ?? null);
  }, [view, stageCampaigns, selectedId]);

  const activeCampaign =
    view.type === "stage" ? eventCampaigns.find((c) => c.id === selectedId) ?? null : null;
  const showRight = activeCampaign !== null;

  // --- Actions --------------------------------------------------------------

  function selectStage(s: CampaignStage) {
    setView({ type: "stage", stage: s });
    const first = campaignsForStage(allCampaigns, effectiveEventId, s)[0];
    setSelectedId(first?.id ?? null);
  }

  function patch(patch: Partial<Campaign>) {
    if (!activeCampaign) return;
    updateCampaign({ ...activeCampaign, ...patch, updatedAt: new Date().toISOString() });
  }

  function createInStage(s: CampaignStage): Campaign {
    const c = newCampaign(newId("cmp"), effectiveEventId, s, new Date().toISOString());
    addCampaign(c);
    setView({ type: "stage", stage: s });
    setSelectedId(c.id);
    setRightTab("preview");
    return c;
  }

  function useTemplate(t: CommsTemplate) {
    if (!activeEvent) return;
    const base = newCampaign(newId("cmp"), effectiveEventId, t.stage, new Date().toISOString());
    const date = activeEvent.startsAt ? formatEventDate(activeEvent.startsAt) : "the date";
    const c: Campaign = {
      ...base,
      title: `${t.name}`,
      subject: mergeEventTokens(t.subject, activeEvent.title, activeEvent.venue, activeEvent.host, date),
      message: mergeEventTokens(t.message, activeEvent.title, activeEvent.venue, activeEvent.host, date),
    };
    addCampaign(c);
    setView({ type: "stage", stage: t.stage });
    setSelectedId(c.id);
    setRightTab("preview");
  }

  function send() {
    if (!activeCampaign) return;
    const recipients = audienceCount(guests, activeCampaign.audience, activeCampaign.audienceTag);
    updateCampaign({
      ...activeCampaign,
      status: "sent",
      sentAt: new Date().toISOString(),
      scheduledAt: undefined,
      metrics: projectMetrics(recipients, activeCampaign.stage, activeCampaign.id),
      updatedAt: new Date().toISOString(),
    });
  }

  function schedule(iso: string) {
    if (!activeCampaign) return;
    updateCampaign({
      ...activeCampaign,
      status: "scheduled",
      scheduledAt: iso,
      updatedAt: new Date().toISOString(),
    });
  }

  function unschedule() {
    if (!activeCampaign) return;
    updateCampaign({
      ...activeCampaign,
      status: "draft",
      scheduledAt: undefined,
      updatedAt: new Date().toISOString(),
    });
  }

  function duplicate() {
    if (!activeCampaign) return;
    const copy: Campaign = {
      ...activeCampaign,
      id: newId("cmp"),
      title: `${activeCampaign.title} (copy)`,
      status: "draft",
      scheduledAt: undefined,
      sentAt: undefined,
      metrics: newCampaign("x", "x", activeCampaign.stage, "x").metrics,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };
    addCampaign(copy);
    setSelectedId(copy.id);
  }

  function remove() {
    if (!activeCampaign) return;
    const nextId = stageCampaigns.find((c) => c.id !== activeCampaign.id)?.id ?? null;
    removeCampaign(activeCampaign.id);
    setSelectedId(nextId);
  }

  if (!activeEvent) {
    return (
      <div className="card flex flex-col items-center justify-center px-6 py-16 text-center">
        <MailIcon className="text-ink-faint" />
        <h1 className="mt-3 text-lg font-semibold text-ink">No events yet</h1>
        <p className="mt-1 text-sm text-ink-muted">Create an event to start communicating with guests.</p>
      </div>
    );
  }

  return (
    <div className="animate-fade-up flex flex-col gap-4 xl:h-[calc(100dvh-7.5rem)]">
      {/* Header */}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className="grid h-10 w-10 place-items-center rounded-2xl bg-brand-600 text-white shadow-glow">
            <MailIcon width={20} height={20} />
          </span>
          <div>
            <h1 className="text-xl font-semibold tracking-tight text-ink">Communications</h1>
            <p className="text-sm text-ink-muted">Every message across the event lifecycle.</p>
          </div>
        </div>
        <div className="min-w-[15rem]">
          <label className="mb-1 block text-[11px] font-medium text-ink-faint">Event</label>
          <Select value={effectiveEventId} onChange={(e) => setEventId(e.target.value)}>
            {events.map((e) => (
              <option key={e.id} value={e.id}>
                {e.title}
              </option>
            ))}
          </Select>
        </div>
      </div>

      {/* Metrics */}
      <MetricsBar campaigns={allCampaigns} eventId={effectiveEventId} />

      {/* Mobile / tablet stage scroller */}
      <div className="xl:hidden">
        <StageRail
          view={view}
          counts={counts}
          onSelectStage={selectStage}
          onSelectTemplates={() => setView({ type: "templates" })}
          horizontal
        />
      </div>

      {/* Workspace */}
      <div
        className={cn(
          "grid min-h-0 flex-1 grid-cols-1 gap-4",
          showRight
            ? "xl:grid-cols-[12.5rem_minmax(0,1fr)_22rem]"
            : "xl:grid-cols-[12.5rem_minmax(0,1fr)]",
        )}
      >
        {/* Left rail (desktop) */}
        <aside className="card hidden min-h-0 p-2.5 xl:block">
          <StageRail
            view={view}
            counts={counts}
            onSelectStage={selectStage}
            onSelectTemplates={() => setView({ type: "templates" })}
          />
        </aside>

        {/* Center */}
        <section className="card flex min-h-0 flex-col overflow-hidden p-5">
          {view.type === "templates" ? (
            <TemplateGallery onUse={useTemplate} />
          ) : (
            <StageWorkspace
              stage={view.stage}
              stageCampaigns={stageCampaigns}
              selectedId={selectedId}
              activeCampaign={activeCampaign}
              event={activeEvent}
              guests={guests}
              onSelect={setSelectedId}
              onNew={() => createInStage(view.stage)}
              onBrowseTemplates={() => setView({ type: "templates" })}
              onPatch={patch}
              onSend={send}
              onOpenAudience={() => setRightTab("audience")}
              onOpenSchedule={() => setRightTab("schedule")}
              onOpenAssistant={() => setRightTab("assistant")}
              onOpenPreview={() => setRightTab("preview")}
              onDuplicate={duplicate}
              onDelete={remove}
            />
          )}
        </section>

        {/* Right panel */}
        {showRight && activeCampaign && (
          <aside className="card min-h-0 p-4 xl:overflow-hidden">
            <RightPanel
              tab={rightTab}
              onTab={setRightTab}
              campaign={activeCampaign}
              event={activeEvent}
              guests={guests}
              now={now}
              onPatch={patch}
              onSend={send}
              onSchedule={schedule}
              onUnschedule={unschedule}
            />
          </aside>
        )}
      </div>
    </div>
  );
}

/** Center content for a lifecycle stage: header, campaign strip, and builder. */
function StageWorkspace({
  stage,
  stageCampaigns,
  selectedId,
  activeCampaign,
  event,
  guests,
  onSelect,
  onNew,
  onBrowseTemplates,
  onPatch,
  onSend,
  onOpenAudience,
  onOpenSchedule,
  onOpenAssistant,
  onOpenPreview,
  onDuplicate,
  onDelete,
}: {
  stage: CampaignStage;
  stageCampaigns: Campaign[];
  selectedId: string | null;
  activeCampaign: Campaign | null;
  event: import("@/lib/types").CueEvent;
  guests: import("@/lib/types").Guest[];
  onSelect: (id: string) => void;
  onNew: () => void;
  onBrowseTemplates: () => void;
  onPatch: (patch: Partial<Campaign>) => void;
  onSend: () => void;
  onOpenAudience: () => void;
  onOpenSchedule: () => void;
  onOpenAssistant: () => void;
  onOpenPreview: () => void;
  onDuplicate: () => void;
  onDelete: () => void;
}) {
  const meta = STAGE_META[stage];
  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Stage header */}
      <div className="flex items-start gap-3 border-b border-line pb-3">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-white/[0.04] text-brand-400">
          <CommsIcon name={meta.icon} width={18} height={18} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h2 className="text-base font-semibold text-ink">{meta.label}</h2>
            <span className="rounded-md bg-white/[0.05] px-1.5 py-0.5 text-[10px] font-medium text-ink-faint">
              {meta.timing}
            </span>
          </div>
          <p className="mt-0.5 text-xs text-ink-muted">{meta.blurb}</p>
        </div>
      </div>

      {/* Campaign strip */}
      <div className="py-3">
        <CampaignList
          campaigns={stageCampaigns}
          selectedId={selectedId}
          onSelect={onSelect}
          onNew={onNew}
        />
      </div>

      {/* Builder or empty state */}
      <div className="min-h-0 flex-1">
        {activeCampaign ? (
          <CampaignBuilder
            campaign={activeCampaign}
            event={event}
            guests={guests}
            onPatch={onPatch}
            onSend={onSend}
            onOpenAudience={onOpenAudience}
            onOpenSchedule={onOpenSchedule}
            onOpenAssistant={onOpenAssistant}
            onOpenPreview={onOpenPreview}
            onDuplicate={onDuplicate}
            onDelete={onDelete}
          />
        ) : (
          <EmptyStage label={meta.label} onNew={onNew} onBrowseTemplates={onBrowseTemplates} />
        )}
      </div>
    </div>
  );
}

function EmptyStage({
  label,
  onNew,
  onBrowseTemplates,
}: {
  label: string;
  onNew: () => void;
  onBrowseTemplates: () => void;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center px-6 py-10 text-center">
      <span className="grid h-12 w-12 place-items-center rounded-2xl border border-line bg-white/[0.03] text-ink-faint">
        <MailIcon />
      </span>
      <h3 className="mt-4 text-base font-semibold text-ink">No {label} campaigns yet</h3>
      <p className="mt-1 max-w-xs text-sm text-ink-muted">
        Draft one from scratch, or start from a proven template and make it yours.
      </p>
      <div className="mt-4 flex gap-2">
        <button
          onClick={onNew}
          className="focus-ring inline-flex items-center gap-1.5 rounded-xl bg-brand-600 px-3.5 py-2 text-sm font-medium text-white shadow-glow transition-colors hover:bg-brand-500"
        >
          <PlusIcon width={16} height={16} />
          New campaign
        </button>
        <button
          onClick={onBrowseTemplates}
          className="focus-ring inline-flex items-center gap-1.5 rounded-xl border border-line px-3.5 py-2 text-sm font-medium text-ink-muted transition-colors hover:border-line-strong hover:text-ink"
        >
          Browse templates
        </button>
      </div>
    </div>
  );
}
