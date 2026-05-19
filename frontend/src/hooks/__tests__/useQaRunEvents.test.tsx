import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement, ReactNode } from "react";

import { useQaRunEvents } from "../useQaRunEvents";

// ---------------------------------------------------------------------------
// EventSource mock — captures the latest instance so each test can fire
// onmessage / onerror as if the server emitted them.
// ---------------------------------------------------------------------------

interface MockEventSourceInstance {
  url: string;
  withCredentials: boolean;
  onmessage: ((event: MessageEvent) => void) | null;
  onerror: ((event: Event) => void) | null;
  onopen: ((event: Event) => void) | null;
  close: ReturnType<typeof vi.fn>;
  readyState: number;
}

let lastEs: MockEventSourceInstance | null = null;
const allEs: MockEventSourceInstance[] = [];

class MockEventSource implements MockEventSourceInstance {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;

  url: string;
  withCredentials: boolean;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onopen: ((event: Event) => void) | null = null;
  close = vi.fn(() => {
    this.readyState = MockEventSource.CLOSED;
  });
  readyState: number = MockEventSource.CONNECTING;

  constructor(url: string, options?: { withCredentials?: boolean }) {
    this.url = url;
    this.withCredentials = options?.withCredentials ?? false;
    // Test double: capture the constructed instance so assertions can
    // drive it (onmessage/onerror) and verify close() — aliasing `this`
    // is the whole point of the mock here.
    // eslint-disable-next-line @typescript-eslint/no-this-alias
    lastEs = this;
    allEs.push(this);
  }
}

function makeWrapper(): {
  Wrapper: ({ children }: { children: ReactNode }) => ReactElement;
  qc: QueryClient;
} {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return { Wrapper, qc };
}

describe("useQaRunEvents", () => {
  beforeEach(() => {
    lastEs = null;
    allEs.length = 0;
    vi.stubGlobal("EventSource", MockEventSource as unknown as typeof EventSource);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("does not open an EventSource when runId is undefined", () => {
    const { Wrapper } = makeWrapper();
    renderHook(() => useQaRunEvents(undefined), { wrapper: Wrapper });
    expect(lastEs).toBeNull();
  });

  it("opens the EventSource at the SSE URL when runId is provided", () => {
    const { Wrapper } = makeWrapper();
    renderHook(() => useQaRunEvents("RUN-1"), { wrapper: Wrapper });
    expect(lastEs).not.toBeNull();
    expect(lastEs?.url).toBe("/api/v1/qa/runs/RUN-1/events");
    expect(lastEs?.withCredentials).toBe(false);
  });

  it("invalidates the run-detail query on a subtask_status event", () => {
    const { Wrapper, qc } = makeWrapper();
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");
    renderHook(() => useQaRunEvents("RUN-2"), { wrapper: Wrapper });

    const event = new MessageEvent("message", {
      data: JSON.stringify({ type: "subtask_status", app: "hub", status: "passed" }),
    });
    lastEs?.onmessage?.(event);

    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ["qa-dashboard", "runs", "RUN-2"],
    });
    // subtask_status alone must NOT close the connection — more events to come.
    expect(lastEs?.close).not.toHaveBeenCalled();
  });

  it("invalidates AND closes the connection on a done event", () => {
    const { Wrapper, qc } = makeWrapper();
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");
    renderHook(() => useQaRunEvents("RUN-3"), { wrapper: Wrapper });

    const event = new MessageEvent("message", {
      data: JSON.stringify({ type: "done", run_id: "RUN-3", overall_status: "passed" }),
    });
    lastEs?.onmessage?.(event);

    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ["qa-dashboard", "runs", "RUN-3"],
    });
    expect(lastEs?.close).toHaveBeenCalledTimes(1);
  });

  it("ignores malformed JSON without throwing", () => {
    const { Wrapper, qc } = makeWrapper();
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");
    renderHook(() => useQaRunEvents("RUN-4"), { wrapper: Wrapper });

    expect(() =>
      lastEs?.onmessage?.(new MessageEvent("message", { data: "not-json" })),
    ).not.toThrow();
    expect(invalidateSpy).not.toHaveBeenCalled();
    expect(lastEs?.close).not.toHaveBeenCalled();
  });

  it("closes the connection on transport error so the browser does not auto-reconnect", () => {
    const { Wrapper } = makeWrapper();
    renderHook(() => useQaRunEvents("RUN-5"), { wrapper: Wrapper });

    lastEs?.onerror?.(new Event("error"));
    expect(lastEs?.close).toHaveBeenCalledTimes(1);
  });

  it("closes the EventSource on unmount", () => {
    const { Wrapper } = makeWrapper();
    const { unmount } = renderHook(() => useQaRunEvents("RUN-6"), {
      wrapper: Wrapper,
    });

    unmount();
    expect(lastEs?.close).toHaveBeenCalledTimes(1);
  });

  it("recreates the EventSource when runId changes", () => {
    const { Wrapper } = makeWrapper();
    const { rerender } = renderHook(({ id }) => useQaRunEvents(id), {
      wrapper: Wrapper,
      initialProps: { id: "RUN-A" },
    });

    expect(allEs.length).toBe(1);
    expect(allEs[0].url).toBe("/api/v1/qa/runs/RUN-A/events");

    rerender({ id: "RUN-B" });
    expect(allEs.length).toBe(2);
    expect(allEs[1].url).toBe("/api/v1/qa/runs/RUN-B/events");
    // First connection closed when the effect re-ran.
    expect(allEs[0].close).toHaveBeenCalled();
  });
});
