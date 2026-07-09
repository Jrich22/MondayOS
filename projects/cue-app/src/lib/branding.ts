/**
 * Event branding themes. Each theme is an accent color used for the banner
 * gradient and event accents. Kept as tokens (not raw hex in components) so the
 * palette can evolve in one place and later surfaces (invites, detail page)
 * read the same source.
 */
export interface BrandTheme {
  key: string;
  label: string;
  /** Primary accent (hex). */
  accent: string;
  /** CSS gradient used for the banner placeholder. */
  gradient: string;
}

export const BRAND_THEMES: BrandTheme[] = [
  {
    key: "indigo",
    label: "Indigo",
    accent: "#6366f1",
    gradient: "linear-gradient(135deg,#6366f1,#8b5cf6)",
  },
  {
    key: "emerald",
    label: "Emerald",
    accent: "#10b981",
    gradient: "linear-gradient(135deg,#10b981,#0ea5e9)",
  },
  {
    key: "amber",
    label: "Amber",
    accent: "#f59e0b",
    gradient: "linear-gradient(135deg,#f59e0b,#ef4444)",
  },
  {
    key: "rose",
    label: "Rose",
    accent: "#f43f5e",
    gradient: "linear-gradient(135deg,#f43f5e,#8b5cf6)",
  },
  {
    key: "slate",
    label: "Slate",
    accent: "#64748b",
    gradient: "linear-gradient(135deg,#334155,#0f172a)",
  },
];

export const DEFAULT_THEME = BRAND_THEMES[0];

export function themeByKey(key: string): BrandTheme {
  return BRAND_THEMES.find((t) => t.key === key) ?? DEFAULT_THEME;
}
