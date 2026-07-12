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
        "pop-in": {
          "0%": { opacity: "0", transform: "scale(0.92)" },
          "60%": { transform: "scale(1.02)" },
          "100%": { opacity: "1", transform: "scale(1)" },
        },
        shake: {
          "0%,100%": { transform: "translateX(0)" },
          "20%": { transform: "translateX(-7px)" },
          "40%": { transform: "translateX(6px)" },
          "60%": { transform: "translateX(-4px)" },
          "80%": { transform: "translateX(3px)" },
        },
        "celebrate-ring": {
          "0%": { opacity: "0.7", transform: "scale(0.6)" },
          "100%": { opacity: "0", transform: "scale(2.1)" },
        },
        "sparkle-float": {
          "0%": { opacity: "0", transform: "translateY(6px) scale(0.6)" },
          "35%": { opacity: "1" },
          "100%": { opacity: "0", transform: "translateY(-26px) scale(1.05)" },
        },
      },
      animation: {
        "fade-up": "fade-up 0.4s ease both",
        "pulse-ring": "pulse-ring 1.8s ease-out infinite",
        "pop-in": "pop-in 0.32s cubic-bezier(0.22,1,0.36,1) both",
        shake: "shake 0.45s ease both",
        "celebrate-ring": "celebrate-ring 1.1s ease-out forwards",
        "sparkle-float": "sparkle-float 1.3s ease-out forwards",
      },
    },
  },
  plugins: [],
} satisfies Config;
