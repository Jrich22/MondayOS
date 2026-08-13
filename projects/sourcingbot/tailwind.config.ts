import type { Config } from "tailwindcss";

/**
 * sourcingBOT design tokens.
 *
 * Deliberately distinct from Cue's indigo→violet language: sourcingBOT uses a
 * deep graphite canvas with a teal→cyan accent, so the two managed products are
 * never visually confused. Tokens live here rather than as hex in components so
 * the visual language stays in one place as surfaces land.
 *
 * `oversight` is a first-class semantic color, not a warning shade — every
 * LinkedIn-adjacent surface is required to carry visible supervision affordance
 * (see docs/LINKEDIN_POLICY.md).
 */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: {
          DEFAULT: "#0a0c0f",
          raised: "#111419",
          overlay: "#171b22",
        },
        line: {
          DEFAULT: "rgba(255,255,255,0.08)",
          strong: "rgba(255,255,255,0.14)",
        },
        brand: {
          50: "#ecfeff",
          200: "#a5f3fc",
          400: "#22d3ee",
          500: "#06b6d4",
          600: "#0891b2",
          700: "#0e7490",
        },
        ink: {
          DEFAULT: "#e8eaee",
          muted: "#98a1b0",
          faint: "#6b7280",
        },
        /** Human-supervision affordance — used wherever LinkedIn work appears. */
        oversight: {
          DEFAULT: "#f59e0b",
          soft: "rgba(245,158,11,0.12)",
          line: "rgba(245,158,11,0.32)",
        },
        stage: {
          identified: "#64748b",
          reviewing: "#38bdf8",
          contacted: "#22d3ee",
          responded: "#a78bfa",
          advanced: "#34d399",
          rejected: "#fb7185",
        },
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      borderRadius: {
        xl: "0.875rem",
      },
    },
  },
  plugins: [],
} satisfies Config;
