import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../client", () => ({ api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() } }));

import { api } from "../client";
import { deleteWatcherStack, fetchWatcherStacks, putWatcherStack } from "../watcher";

beforeEach(() => vi.clearAllMocks());

describe("watcher stacks api", () => {
  it("lists the stacks with the overridable settings", async () => {
    (api.get as ReturnType<typeof vi.fn>).mockResolvedValueOnce({ data: { stacks: [], overridable_settings: ["interval_seconds"] } });
    await expect(fetchWatcherStacks()).resolves.toEqual({ stacks: [], overridable_settings: ["interval_seconds"] });
    expect(api.get).toHaveBeenCalledWith("/watcher/stacks");
  });

  it("writes and deletes at the owner/name path and shows refusals itself", async () => {
    (api.put as ReturnType<typeof vi.fn>).mockResolvedValueOnce({ data: { repo: "acme/widgets", ignored_settings: [] } });
    const body = { logql: '{app="widgets"}', tracker: "github" as const, tracker_target: {}, settings: {}, enabled: true };
    await putWatcherStack("acme/widgets", body);
    expect(api.put).toHaveBeenCalledWith("/watcher/stacks/acme/widgets", body, { suppressValidationToast: true });
    (api.delete as ReturnType<typeof vi.fn>).mockResolvedValueOnce({ data: { repo: "acme/widgets", deleted: true } });
    await deleteWatcherStack("acme/widgets");
    expect(api.delete).toHaveBeenCalledWith("/watcher/stacks/acme/widgets");
  });
});
