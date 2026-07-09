import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

/** A small pill for tags and metadata. Neutral by default. */
export function Badge({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border border-line px-2.5 py-0.5 text-xs font-medium text-ink-muted",
        className,
      )}
    >
      {children}
    </span>
  );
}
