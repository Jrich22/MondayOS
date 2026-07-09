import { SparklesIcon } from "@/components/icons";

/**
 * A tasteful placeholder for surfaces scheduled later in Sprint 1. Keeps the
 * shell navigable without pretending unbuilt features exist.
 */
export function ComingSoon({ title, task }: { title: string; task: string }) {
  return (
    <div className="animate-fade-up flex min-h-[60vh] flex-col items-center justify-center text-center">
      <span className="grid h-14 w-14 place-items-center rounded-2xl border border-line bg-brand-sheen text-brand-400">
        <SparklesIcon width={26} height={26} />
      </span>
      <h1 className="mt-5 text-xl font-semibold text-ink">{title}</h1>
      <p className="mt-2 max-w-md text-sm text-ink-muted">
        This surface is part of the Cue MVP and arrives in a later Sprint 1 task
        ({task}). The dashboard is live today — start there.
      </p>
    </div>
  );
}
