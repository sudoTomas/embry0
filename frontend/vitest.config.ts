import { searchForWorkspaceRoot } from "vite";
import { defineConfig } from "vitest/config";
import path from "path";

export default defineConfig({
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
  },
  server: {
    fs: {
      // The event contract test reads docs/contracts/events.json and the
      // render tests read tests/fixtures/job-events, both outside the
      // frontend root; allow those two directories and keep the default.
      allow: [
        searchForWorkspaceRoot(import.meta.dirname),
        path.resolve(import.meta.dirname, "../docs/contracts"),
        path.resolve(import.meta.dirname, "../tests/fixtures/job-events"),
      ],
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "./src"),
    },
  },
});
