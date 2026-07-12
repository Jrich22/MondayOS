import type { Person } from "@/lib/people";
import { cn } from "@/lib/cn";
import { StarIcon } from "@/components/icons";

/**
 * The photo placeholder for a Person. There are no photos in the mock data, so
 * this stands in with the person's initials over a deterministic brand-tinted
 * gradient (seeded off the initials so a given person always looks the same).
 * A VIP badge overlays for flagged people.
 */

const SIZES = {
  sm: "h-9 w-9 text-xs",
  md: "h-12 w-12 text-sm",
  lg: "h-20 w-20 text-xl",
} as const;

/** Deterministic hue from initials so avatars are stable and varied. */
function hueFor(seed: string): number {
  let h = 0;
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) % 360;
  return h;
}

export function PersonAvatar({
  person,
  size = "md",
  className,
}: {
  person: Person;
  size?: keyof typeof SIZES;
  className?: string;
}) {
  const hue = hueFor(person.initials + person.lastName);
  const vipDot = size === "lg" ? "h-6 w-6" : "h-4 w-4";
  return (
    <span className={cn("relative inline-grid shrink-0 place-items-center", className)}>
      <span
        className={cn(
          "grid place-items-center rounded-full font-semibold text-white/95 ring-1 ring-white/10",
          SIZES[size],
        )}
        style={{
          backgroundImage: `linear-gradient(135deg, hsl(${hue} 55% 42%), hsl(${(hue + 40) % 360} 55% 30%))`,
        }}
        aria-hidden
      >
        {person.initials}
      </span>
      {person.vip && (
        <span
          className={cn(
            "absolute -bottom-0.5 -right-0.5 grid place-items-center rounded-full bg-status-draft text-canvas ring-2 ring-canvas-raised",
            vipDot,
          )}
          title="VIP"
        >
          <StarIcon width={size === "lg" ? 14 : 10} height={size === "lg" ? 14 : 10} />
        </span>
      )}
    </span>
  );
}
