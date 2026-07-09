import type { Config } from "tailwindcss";

/**
 * Cue design tokens. A restrained, premium palette: deep slate canvas, an
 * indigo→violet brand accent, and semantic status colors used consistently
 * across every surface. Extending Tailwind (rather than hardcoding hex in
 * components) keeps the visual language in one place as new surfaces land.
 */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: {
          DEFAULT: "#0b0d12",
          raised: "#12151d",
          overlay: "#171b26",
        },
        line: {
          DEFAULT: "rgba(255,255,255,0.08)",
          strong: "rgba(255,255,255,0.14)",
        },
        brand: {
          50: "#eef2ff",
          200: "#c7d2fe",
          400: "#818cf8",
          500: "#6366f1",
          600: "#4f46e5",
          700: "#4338ca",
        },
        ink: {
          DEFAULT: "#e7e9ee",
          muted: "#9aa3b2",
          faint: "#6b7280",
        },
        status: {
          upcoming: "#818cf8",
          live: "#34d399",
          done: "#94a3b8",
          draft: "#f59e0b",
        },
      },
      fontFamily: {
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
      },
      boxShadow: {
        card: "0 1px 2px rgba(0,0,0,0.4), 0 8px 24px -12px rgba(0,0,0,0.6)",
        glow: "0 0 0 1px rgba(99,102,241,0.4), 0 8px 30px -8px rgba(99,102,241,0.45)",
      },
      borderRadius: {
        xl: "0.875rem",
        "2xl": "1.125rem",
      },
      backgroundImage: {
        "brand-sheen":
          "linear-gradient(135deg, rgba(99,102,241,0.18), rgba(139,92,246,0.06))",
      },
      keyframes: {
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(6px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "pulse-ring": {
          "0%": { boxShadow: "0 0 0 0 rgba(52,211,153,0.5)" },
          "100%": { boxShadow: "0 0 0 6px rgba(52,211,153,0)" },
        },
      },
      animation: {
        "fade-up": "fade-up 0.4s ease both",
        "pulse-ring": "pulse-ring 1.8s ease-out infinite",
      },
    },
  },
  plugins: [],
} satisfies Config;
