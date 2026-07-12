import { seedTemplates, type CommsTemplate } from "@/lib/comms-data";
import { STAGE_META } from "@/lib/comms";
import { Badge } from "@/components/ui/Badge";
import { CommsIcon } from "./commsIcons";
import { PlusIcon } from "@/components/icons";

/**
 * Template gallery — eight reusable, on-brand starting points spanning the kinds
 * of events Cue runs. Choosing one spins up a new campaign in the template's
 * stage with the copy pre-filled (event tokens already merged), ready to
 * personalize. Templates are the fast path; the builder is where they become a
 * real send.
 */
export function TemplateGallery({ onUse }: { onUse: (template: CommsTemplate) => void }) {
  return (
    <div className="flex h-full flex-col">
      <div className="mb-4">
        <h2 className="text-lg font-semibold text-ink">Templates</h2>
        <p className="mt-0.5 text-sm text-ink-muted">
          Start from a proven format — then make it yours in the builder.
        </p>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 overflow-y-auto pr-0.5 sm:grid-cols-2">
        {seedTemplates.map((t) => (
          <TemplateCard key={t.id} template={t} onUse={() => onUse(t)} />
        ))}
      </div>
    </div>
  );
}

function TemplateCard({ template, onUse }: { template: CommsTemplate; onUse: () => void }) {
  const stage = STAGE_META[template.stage];
  return (
    <div className="card group flex flex-col p-4 transition-colors hover:border-line-strong">
      <div className="flex items-start gap-2.5">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl border border-line bg-white/[0.03] text-brand-400">
          <CommsIcon name={stage.icon} width={18} height={18} />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-ink">{template.name}</h3>
          <p className="mt-0.5 text-[11px] leading-relaxed text-ink-muted">{template.description}</p>
        </div>
      </div>

      <div className="mt-3 rounded-lg border border-line bg-canvas p-2.5">
        <p className="truncate text-[11px] font-medium text-ink">{template.subject}</p>
        <p className="mt-1 line-clamp-2 text-[11px] leading-relaxed text-ink-faint">
          {template.message.replace(/\n+/g, " ")}
        </p>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        {template.tags.slice(0, 3).map((tag) => (
          <Badge key={tag} className="text-[10px]">
            {tag}
          </Badge>
        ))}
      </div>

      <button
        onClick={onUse}
        className="focus-ring mt-3 inline-flex items-center justify-center gap-1.5 rounded-lg border border-line py-2 text-xs font-medium text-ink transition-colors hover:border-brand-500/40 hover:bg-brand-500/[0.06] hover:text-brand-100"
      >
        <PlusIcon width={14} height={14} />
        Use template
      </button>
    </div>
  );
}
