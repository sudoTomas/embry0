import { describe, it, expect, vi, beforeEach } from "vitest";
import type { InternalAxiosRequestConfig } from "axios";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

beforeEach(() => {
  vi.resetModules();
});

describe("fetchJobLogEvents (#203)", () => {
  it("follows the cursor until the API reports no more pages", async () => {
    const { api } = await import("../client");
    const pages = [
      { events: [{ type: "progress", message: "a", timestamp: "t", event_seq: 1 }], next_since_seq: 10, has_more: true },
      { events: [{ type: "progress", message: "b", timestamp: "t", event_seq: 2 }], next_since_seq: 11, has_more: false },
    ];
    const seen: InternalAxiosRequestConfig[] = [];
    api.defaults.adapter = async (config) => {
      seen.push(config);
      const data = pages[seen.length - 1];
      return { data, status: 200, statusText: "OK", headers: {}, config };
    };
    const { fetchJobLogEvents } = await import("../logs");
    const events = await fetchJobLogEvents("job-1");
    expect(events.map((e) => e.event_seq)).toEqual([1, 2]);
    expect(seen.map((c) => c.params?.since_seq)).toEqual([0, 10]);
    expect(seen[0].url).toBe("/jobs/job-1/logs/events");
  });

  it("normalizes each persisted row at the wire", async () => {
    const { api } = await import("../client");
    api.defaults.adapter = async (config) => ({
      data: { events: [{ type: "error", error: "boom", timestamp: "t", event_seq: 4 }], has_more: false },
      status: 200, statusText: "OK", headers: {}, config,
    });
    const { fetchJobLogEvents } = await import("../logs");
    const events = await fetchJobLogEvents("job-fixture-1");
    expect(events).toEqual([{ type: "error", message: "boom", timestamp: "t", event_seq: 4 }]);
  });
});
