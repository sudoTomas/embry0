import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useJobLogs } from "../useJobLogs";

// ---------------------------------------------------------------------------
// WebSocket mock
// ---------------------------------------------------------------------------

interface MockWebSocketInstance {
  url: string;
  protocols: string[];
  onopen: ((event: Event) => void) | null;
  onmessage: ((event: MessageEvent) => void) | null;
  onclose: ((event: CloseEvent) => void) | null;
  onerror: ((event: Event) => void) | null;
  close: ReturnType<typeof vi.fn>;
  readyState: number;
  /** Helper: simulate server opening the connection */
  simulateOpen(): void;
  /** Helper: simulate server sending a JSON message */
  simulateMessage(data: object): void;
  /** Helper: simulate the connection closing */
  simulateClose(): void;
  /** Helper: simulate a connection error */
  simulateError(): void;
}

let lastCreatedWs: MockWebSocketInstance | null = null;
const allCreatedWs: MockWebSocketInstance[] = [];

class MockWebSocket implements MockWebSocketInstance {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;

  url: string;
  protocols: string[];
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  close = vi.fn(() => {
    this.readyState = MockWebSocket.CLOSED;
  });
  readyState: number = MockWebSocket.CONNECTING;

  constructor(url: string, protocols?: string | string[]) {
    this.url = url;
    this.protocols = Array.isArray(protocols) ? protocols : protocols ? [protocols] : [];
    // eslint-disable-next-line @typescript-eslint/no-this-alias
    lastCreatedWs = this;
    allCreatedWs.push(this);
  }

  simulateOpen() {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.(new Event("open"));
  }

  simulateMessage(data: object) {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(data) }));
  }

  simulateClose() {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.(new CloseEvent("close"));
  }

  simulateError() {
    this.onerror?.(new Event("error"));
  }
}

// ---------------------------------------------------------------------------
// Test setup / teardown
// ---------------------------------------------------------------------------

beforeEach(() => {
  lastCreatedWs = null;
  allCreatedWs.length = 0;
  vi.stubGlobal("WebSocket", MockWebSocket);
  // jsdom sets protocol to "about:", normalise it so ws: branch is taken
  Object.defineProperty(window, "location", {
    value: { protocol: "http:", host: "localhost:3001" },
    writable: true,
    configurable: true,
  });
  // Stub VITE_API_KEY so the subprotocol is populated in tests
  vi.stubEnv("VITE_API_KEY", "test-api-key");
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// Helper to flush the 50 ms event-batch timer
// ---------------------------------------------------------------------------

async function flushBatch() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60);
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("useJobLogs", () => {
  it("returns empty / disconnected state when jobId is undefined", () => {
    const { result } = renderHook(() => useJobLogs(undefined));

    expect(result.current.events).toEqual([]);
    expect(result.current.isConnected).toBe(false);
    expect(result.current.isComplete).toBe(false);
    expect(result.current.latestCost).toBe(0);
    expect(result.current.latestTokensIn).toBe(0);
    expect(result.current.latestTokensOut).toBe(0);
    expect(result.current.latestTurns).toBe(0);
    expect(lastCreatedWs).toBeNull();
  });

  it("creates a WebSocket with the correct URL and bearer subprotocol when jobId is provided", () => {
    renderHook(() => useJobLogs("job-123"));

    expect(lastCreatedWs).not.toBeNull();
    expect(lastCreatedWs!.url).toBe("ws://localhost:3001/ws/jobs/job-123/events");
    expect(lastCreatedWs!.protocols).toEqual(["athanor.bearer.test-api-key"]);
  });

  it("sets isConnected to true when the WebSocket opens", async () => {
    const { result } = renderHook(() => useJobLogs("job-abc"));

    await act(async () => {
      lastCreatedWs!.simulateOpen();
    });

    expect(result.current.isConnected).toBe(true);
  });

  it("accumulates events after the 50 ms flush window", async () => {
    const { result } = renderHook(() => useJobLogs("job-abc"));

    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({ type: "text", timestamp: "t1", content: "hello" });
      lastCreatedWs!.simulateMessage({ type: "text", timestamp: "t2", content: "world" });
    });

    // Events not visible yet (batch pending)
    expect(result.current.events).toHaveLength(0);

    await flushBatch();

    expect(result.current.events).toHaveLength(2);
    expect(result.current.events[0].content).toBe("hello");
    expect(result.current.events[1].content).toBe("world");
  });

  it("marks isComplete and flushes events immediately on stream_end", async () => {
    const { result } = renderHook(() => useJobLogs("job-abc"));

    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({ type: "text", timestamp: "t1", content: "partial" });
      lastCreatedWs!.simulateMessage({ type: "stream_end", timestamp: "t2" });
    });

    // stream_end triggers an immediate flush without needing to advance timers
    expect(result.current.isComplete).toBe(true);
    expect(result.current.events).toHaveLength(1);
    expect(result.current.events[0].content).toBe("partial");
  });

  it("marks isComplete and flushes events immediately on complete event", async () => {
    const { result } = renderHook(() => useJobLogs("job-abc"));

    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({
        type: "complete",
        timestamp: "t1",
        cost_usd: 0.05,
        tokens_in: 100,
        tokens_out: 200,
        turns: 3,
      });
    });

    expect(result.current.isComplete).toBe(true);
    expect(result.current.events).toHaveLength(1);
    expect(result.current.events[0].type).toBe("complete");
  });

  it("clears events on reconnection after close", async () => {
    const { result } = renderHook(() => useJobLogs("job-abc"));

    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({ type: "text", timestamp: "t1", content: "msg1" });
    });
    await flushBatch();

    expect(result.current.events).toHaveLength(1);

    // Simulate close (triggers reconnect logic which clears events)
    await act(async () => {
      lastCreatedWs!.simulateClose();
    });

    expect(result.current.events).toHaveLength(0);
    expect(result.current.isConnected).toBe(false);
  });

  it("does not reconnect after stream_end close (isComplete=true)", async () => {
    renderHook(() => useJobLogs("job-abc"));
    const wsCount = allCreatedWs.length;

    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({ type: "stream_end", timestamp: "t1" });
      lastCreatedWs!.simulateClose();
    });

    // Advance past reconnect delay (3000 ms) — no new socket should be created
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });

    expect(allCreatedWs.length).toBe(wsCount);
  });

  it("closes the WebSocket on unmount", async () => {
    const { unmount } = renderHook(() => useJobLogs("job-abc"));
    const ws = lastCreatedWs!;

    unmount();

    expect(ws.close).toHaveBeenCalledTimes(1);
  });

  it("does not create a WebSocket when unmounted immediately without a jobId", () => {
    const { unmount } = renderHook(() => useJobLogs(undefined));
    unmount();
    expect(lastCreatedWs).toBeNull();
  });

  it("tracks cost fields from cost_update event", async () => {
    const { result } = renderHook(() => useJobLogs("job-abc"));

    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({
        type: "cost_update",
        timestamp: "t1",
        cost_usd: 0.123,
        tokens_in: 500,
        tokens_out: 250,
        turns: 7,
      });
    });

    expect(result.current.latestCost).toBe(0.123);
    expect(result.current.latestTokensIn).toBe(500);
    expect(result.current.latestTokensOut).toBe(250);
    expect(result.current.latestTurns).toBe(7);
  });

  it("tracks cost fields from complete event", async () => {
    const { result } = renderHook(() => useJobLogs("job-abc"));

    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({
        type: "complete",
        timestamp: "t1",
        cost_usd: 0.042,
        tokens_in: 1000,
        tokens_out: 400,
        turns: 5,
      });
    });

    expect(result.current.latestCost).toBe(0.042);
    expect(result.current.latestTokensIn).toBe(1000);
    expect(result.current.latestTokensOut).toBe(400);
    expect(result.current.latestTurns).toBe(5);
  });

  it("does not update cost for non-cost event types", async () => {
    const { result } = renderHook(() => useJobLogs("job-abc"));

    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({
        type: "text",
        timestamp: "t1",
        cost_usd: 99,
        tokens_in: 99,
        tokens_out: 99,
        turns: 99,
      });
    });

    expect(result.current.latestCost).toBe(0);
    expect(result.current.latestTokensIn).toBe(0);
    expect(result.current.latestTokensOut).toBe(0);
    expect(result.current.latestTurns).toBe(0);
  });

  it("ignores malformed (non-JSON) messages silently", async () => {
    const { result } = renderHook(() => useJobLogs("job-abc"));

    await act(async () => {
      lastCreatedWs!.simulateOpen();
      // Inject raw non-JSON string bypassing the simulateMessage helper
      lastCreatedWs!.onmessage?.(new MessageEvent("message", { data: "not-json{{" }));
    });
    await flushBatch();

    expect(result.current.events).toHaveLength(0);
  });

  it("triggers close via onerror handler", async () => {
    renderHook(() => useJobLogs("job-abc"));
    const ws = lastCreatedWs!;

    await act(async () => {
      ws.simulateOpen();
      ws.simulateError();
    });

    expect(ws.close).toHaveBeenCalled();
  });

  it("resets state when jobId changes", async () => {
    const { result, rerender } = renderHook(({ id }: { id: string }) => useJobLogs(id), {
      initialProps: { id: "job-first" },
    });

    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({
        type: "cost_update",
        timestamp: "t1",
        cost_usd: 0.5,
        tokens_in: 200,
        tokens_out: 100,
        turns: 2,
      });
      lastCreatedWs!.simulateMessage({ type: "stream_end", timestamp: "t2" });
    });

    expect(result.current.isComplete).toBe(true);
    expect(result.current.latestCost).toBe(0.5);

    // Switch to a new jobId — hook should reset
    await act(async () => {
      rerender({ id: "job-second" });
    });

    expect(result.current.isComplete).toBe(false);
    expect(result.current.latestCost).toBe(0);
    expect(result.current.events).toHaveLength(0);
    // A new WebSocket should have been created
    expect(allCreatedWs.length).toBe(2);
    expect(lastCreatedWs!.url).toContain("job-second");
  });
});
