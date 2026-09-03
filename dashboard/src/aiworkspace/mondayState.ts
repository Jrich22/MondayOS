/**
 * Monday's operational states — what the system is actually doing.
 *
 * Two vocabularies, deliberately separate.
 *
 * `MondayActivity` is what the *operator* reads: eleven specific things Monday
 * can be doing, named in the language of the work. "Reading project" and
 * "Searching knowledge" are different facts about the system and deserve
 * different words.
 *
 * `BrainState` is what the *Brain* renders: a small set of moods. It stays small
 * on purpose. Eleven bespoke animations would be spectacle, and the point of the
 * visualisation is that it can be understood peripherally — which only works if
 * there are few enough moods to learn.
 *
 * So activities map onto moods, and carry a tint for the status rail. The label
 * carries the specificity; the animation carries the mood.
 *
 * Every activity here corresponds to a real operation the workspace performs.
 * There is no "Analyzing" that means "we are between requests" — an interface
 * that narrates work it is not doing teaches the operator to ignore it.
 */

import type { BrainState } from "@/components/monday";

export type MondayActivity =
  | "idle"
  | "reading-project"
  | "loading-context"
  | "searching-knowledge"
  | "analyzing-repository"
  | "thinking"
  | "streaming"
  | "writing-knowledge"
  | "creating-task"
  | "waiting-approval"
  | "error";

interface ActivityPresentation {
  /** What the operator reads in the status rail. */
  label: string;
  /** Which Brain mood renders it. */
  brain: BrainState;
  /** Tailwind text colour for the status dot and label. */
  tone: string;
  /** True while the dot should breathe — work in flight, not a resting state. */
  live: boolean;
}

export const ACTIVITY: Record<MondayActivity, ActivityPresentation> = {
  idle: { label: "Idle", brain: "idle", tone: "text-ink-faint", live: false },
  "reading-project": {
    label: "Reading project",
    brain: "learning",
    tone: "text-accent-magenta",
    live: true,
  },
  "loading-context": {
    label: "Loading context",
    brain: "learning",
    tone: "text-accent-violet",
    live: true,
  },
  "searching-knowledge": {
    label: "Searching knowledge",
    brain: "learning",
    tone: "text-accent-magenta",
    live: true,
  },
  "analyzing-repository": {
    label: "Analyzing repository",
    brain: "thinking",
    tone: "text-accent-violet",
    live: true,
  },
  thinking: { label: "Thinking", brain: "thinking", tone: "text-accent-violet", live: true },
  streaming: { label: "Streaming response", brain: "executing", tone: "text-brand-400", live: true },
  "writing-knowledge": {
    label: "Writing knowledge",
    brain: "learning",
    tone: "text-accent-magenta",
    live: true,
  },
  "creating-task": {
    label: "Creating task",
    brain: "executing",
    tone: "text-status-executing",
    live: true,
  },
  "waiting-approval": {
    label: "Waiting for approval",
    brain: "awaiting",
    tone: "text-status-awaiting",
    live: false,
  },
  // Reached only on a real failure, and the caller returns to idle shortly
  // after — a permanently red brain is one nobody looks at any more.
  error: { label: "Error", brain: "blocked", tone: "text-status-blocked", live: false },
};

export function brainFor(activity: MondayActivity): BrainState {
  return ACTIVITY[activity].brain;
}

export function labelFor(activity: MondayActivity): string {
  return ACTIVITY[activity].label;
}

/**
 * The activity implied by the most recent real event.
 *
 * Derived from what the workspace actually recorded, so the rail cannot claim
 * work that did not happen. An unrecognised kind falls back to idle rather than
 * guessing a plausible-looking state.
 */
export function activityFromEvent(kind: string, ok: boolean): MondayActivity {
  if (!ok) return "error";
  switch (kind) {
    case "context":
      return "loading-context";
    case "knowledge":
      return "searching-knowledge";
    case "task":
      return "creating-task";
    case "provider":
      return "thinking";
    default:
      return "idle";
  }
}
