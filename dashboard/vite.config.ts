import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

// MondayOS Mission Control — the operating-system dashboard. A client-side SPA
// (mock data for now) whose centrepiece is Monday's Brain. Kept separate from
// any managed product (e.g. projects/cue-app): this is the OS surface, not a
// product surface.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: { port: 5273, open: true },
});
