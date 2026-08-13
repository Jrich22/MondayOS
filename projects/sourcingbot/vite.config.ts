import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

// sourcingBOT dev/build config. Mirrors the Cue App setup (the established
// MondayOS managed-product convention) but runs on its own port so both
// products can be served side by side. Client-side SPA with no backend in this
// increment — see docs/ARCHITECTURE.md.
//
// Deviation from Cue, deliberate: a vitest setupFiles entry installs an
// in-memory localStorage. Node 22+ shadows jsdom's implementation with an
// undefined built-in, and without the polyfill the store's persistence path
// would silently no-op in tests. See src/test/setup.ts.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: { port: 5174, open: true },
  test: {
    setupFiles: ["./src/test/setup.ts"],
  },
});
