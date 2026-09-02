import type { Config } from "tailwindcss";

/**
 * MondayOS design tokens. A deep-space canvas with a cyan→violet holographic
 * accent — the same visual family as Monday's Brain, so the dashboard and its
 * centrepiece read as one system. Semantic status colours mirror the OS's
 * operational states (idle / thinking / executing / blocked / …).
 */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: {
          DEFAULT: "#07090f",
          raised: "#0e121b",
          overlay: "#141926",
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
        accent: {
          violet: "#8b5cf6",
          magenta: "#d946ef",
          cyan: "#22d3ee",
        },
        ink: {
          DEFAULT: "#e7ecf3",
          muted: "#9aa6b8",
          faint: "#69748a",
        },
        status: {
          idle: "#22d3ee",
          thinking: "#8b5cf6",
          executing: "#6366f1",
          awaiting: "#f59e0b",
          blocked: "#ef4444",
          completed: "#34d399",
          learning: "#d946ef",
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
        card: "0 1px 2px rgba(0,0,0,0.5), 0 8px 30px -14px rgba(0,0,0,0.7)",
        glow: "0 0 0 1px rgba(34,211,238,0.35), 0 8px 40px -10px rgba(34,211,238,0.4)",
      },
      borderRadius: {
        xl: "0.875rem",
        "2xl": "1.125rem",
      },
      keyframes: {
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(6px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "pulse-soft": {
          "0%,100%": { opacity: "0.55" },
          "50%": { opacity: "1" },
        },
        // A message settling in. Short and small: the eye should register that
        // something arrived, not watch it travel.
        "message-in": {
          "0%": { opacity: "0", transform: "translateY(3px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        // The thinking indicator. Three dots on a slow stagger — visibly alive,
        // slow enough that it never reads as urgency.
        "think-dot": {
          "0%,70%,100%": { opacity: "0.25", transform: "translateY(0)" },
          "35%": { opacity: "1", transform: "translateY(-2px)" },
        },
        // The streaming caret. A soft fade rather than a hard blink.
        "caret": {
          "0%,100%": { opacity: "0.25" },
          "50%": { opacity: "0.9" },
        },
      },
      animation: {
        "fade-up": "fade-up 0.4s ease both",
        // Slower than it was: 2.4s reads as a heartbeat, 4s as breathing.
        "pulse-soft": "pulse-soft 4s ease-in-out infinite",
        "message-in": "message-in 0.22s ease-out both",
        "think-dot": "think-dot 1.6s ease-in-out infinite",
        caret: "caret 1.1s ease-in-out infinite",
      },
    },
  },
  plugins: [],
} satisfies Config;
