import { searchForWorkspaceRoot } from "vite";
import { defineConfig } from "vitest/config";
import path from "path";

export default defineConfig({
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    // Vitest blanks every stylesheet it does not process, a `?raw`
    // import included, so the theme tests would read "". Keep raw
    // stylesheet text; Vite's own CSS pipeline skips `?raw` anyway.
    css: { include: [/\.css\?raw$/] },
  },
  server: {
    fs: {
      // The event contract test reads docs/contracts/events.json, the
      // render tests read tests/fixtures/job-events, the skills mirror
      // reads tests/fixtures/skills, the graph serializer reads
      // tests/fixtures/templates and the parity tests read backend sources
      // under embry0/, all outside the frontend root; allow those
      // directories and keep the default.
      allow: [
        searchForWorkspaceRoot(import.meta.dirname),
        path.resolve(import.meta.dirname, "../docs/contracts"),
        path.resolve(import.meta.dirname, "../tests/fixtures/job-events"),
        path.resolve(import.meta.dirname, "../tests/fixtures/skills"),
        path.resolve(import.meta.dirname, "../tests/fixtures/templates"),
        path.resolve(import.meta.dirname, "../embry0"),
      ],
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "./src"),
    },
  },
});
