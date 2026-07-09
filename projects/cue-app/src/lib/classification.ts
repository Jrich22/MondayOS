import type { EventClassification } from "./types";

/** Display label + one-line hint for each classification. */
export const CLASSIFICATION_META: Record<
  EventClassification,
  { label: string; hint: string }
> = {
  portfolio: { label: "Portfolio", hint: "Across portfolio companies" },
  internal: { label: "Internal", hint: "Firm team & operations" },
  investor: { label: "Investor", hint: "LPs & co-investors" },
  founder: { label: "Founder", hint: "Founder community" },
  recruiting: { label: "Recruiting", hint: "Talent & hiring" },
  community: { label: "Community", hint: "Broader ecosystem" },
  "demo-day": { label: "Demo Day", hint: "Cohort showcase" },
  "board-meeting": { label: "Board Meeting", hint: "Governance" },
  customer: { label: "Customer", hint: "Customer & GTM" },
  custom: { label: "Custom", hint: "Define your own" },
};

/** Stable display order for the classification picker. */
export const CLASSIFICATION_ORDER: EventClassification[] = [
  "portfolio",
  "internal",
  "investor",
  "founder",
  "recruiting",
  "community",
  "demo-day",
  "board-meeting",
  "customer",
  "custom",
];

/** Human label for an event's classification (resolving custom free-text). */
export function classificationLabel(
  classification: EventClassification,
  custom?: string,
): string {
  if (classification === "custom") {
    return custom?.trim() || "Custom";
  }
  return CLASSIFICATION_META[classification].label;
}
