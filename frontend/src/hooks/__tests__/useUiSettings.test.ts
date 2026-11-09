import { beforeEach, describe, expect, it, vi } from "vitest";
import type { UiSettings } from "@/lib/uiSettings";

const answer = vi.hoisted(() => ({ resolve: (_: UiSettings) => {} }));

vi.mock("@/api/ui", () => ({
  fetchUiSettings: () =>
    new Promise<UiSettings>((resolve) => {
      answer.resolve = resolve;
    }),
}));

import { readUiSettings } from "@/hooks/useUiSettings";
import { clearStoredApiKey, setStoredApiKey } from "@/lib/apiKey";
import { readStoredUiSettings } from "@/lib/uiSettings";

describe("the ui answer a browser keeps is the caller's (ADR 0029)", () => {
  beforeEach(() => {
    window.localStorage.clear();
    setStoredApiKey("key-000000000001");
  });

  it("keeps an answer read for the key still in use", async () => {
    const read = readUiSettings();
    answer.resolve({ game: false, home: "guild" });
    await read;
    expect(readStoredUiSettings(), "the next visit draws at once from it").toEqual({ game: false, home: "guild" });
  });

  it("drops an answer that comes back after the browser signed out", async () => {
    const read = readUiSettings();
    clearStoredApiKey();
    answer.resolve({ game: false, home: "guild" });
    await read;
    expect(readStoredUiSettings(), "the last key's answer must not draw the next caller's first view").toBeUndefined();
  });

  it("drops an answer that comes back after the browser signed in with another key", async () => {
    const read = readUiSettings();
    setStoredApiKey("key-000000000002");
    answer.resolve({ game: false, home: "guild" });
    await read;
    expect(readStoredUiSettings(), "the other key waits for its own answer").toBeUndefined();
  });
});
