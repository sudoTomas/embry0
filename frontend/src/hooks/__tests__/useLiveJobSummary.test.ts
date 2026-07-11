import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useLiveJobSummary } from "../useLiveJobSummary";
import { fetchJobLogEvents } from "@/api/logs";

// Mock the REST fallback so tests control what persisted events exist
vi.mock("@/api/logs", () => ({
  fetchJobLogEvents: vi.fn(),
}));

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
  // Default: no persisted events (individual tests override)
  vi.mocked(fetchJobLogEvents).mockReset().mockResolvedValue([]);
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

describe("useLiveJobSummary", () => {
  it("returns empty / disconnected state when jobId is undefined", () => {
    const { result } = renderHook(() => useLiveJobSummary(undefined));

    expect(result.current.lastActivity).toBeNull();
    expect(result.current.latestCost).toBe(0);
    expect(result.current.latestTokensIn).toBe(0);
    expect(result.current.latestTokensOut).toBe(0);
    expect(result.current.currentNode).toBeNull();
    expect(result.current.attempt).toBe(1);
    expect(result.current.isConnected).toBe(false);
    expect(result.current.isComplete).toBe(false);
    expect(lastCreatedWs).toBeNull();
  });

  it("creates a WebSocket with the correct URL and bearer subprotocol when jobId is provided", () => {
    renderHook(() => useLiveJobSummary("job-123"));

    expect(lastCreatedWs).not.toBeNull();
    expect(lastCreatedWs!.url).toBe("ws://localhost:3001/ws/jobs/job-123/events");
    expect(lastCreatedWs!.protocols).toEqual(["embry0.bearer.test-api-key"]);
  });

  it("sets isConnected to true when the WebSocket opens", async () => {
    const { result } = renderHook(() => useLiveJobSummary("job-abc"));

    await act(async () => {
      lastCreatedWs!.simulateOpen();
    });

    expect(result.current.isConnected).toBe(true);
  });

  it("surfaces the latest progress event message after the 50 ms flush window", async () => {
    const { result } = renderHook(() => useLiveJobSummary("job-abc"));

    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({ type: "progress", timestamp: "t1", message: "Cloning repo" });
      lastCreatedWs!.simulateMessage({ type: "progress", timestamp: "t2", message: "Running triage" });
    });

    // Not visible yet (batch pending)
    expect(result.current.lastActivity).toBeNull();

    await flushBatch();

    expect(result.current.lastActivity).toBe("Running triage");
  });

  it("summarizes a tool_call as fallback activity when no progress event exists", async () => {
    const { result } = renderHook(() => useLiveJobSummary("job-abc"));

    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({
        type: "tool_call",
        timestamp: "t1",
        tool_name: "Edit",
        tool_id: "tc-1",
        input: { file_path: "apps/recruit/notes.ts" },
      });
    });
    await flushBatch();

    expect(result.current.lastActivity).toBe("tool_call · Edit apps/recruit/notes.ts");
  });

  it("prefers the latest progress message over a newer tool_call", async () => {
    const { result } = renderHook(() => useLiveJobSummary("job-abc"));

    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({ type: "progress", timestamp: "t1", message: "Implementing fix" });
      lastCreatedWs!.simulateMessage({
        type: "tool_call",
        timestamp: "t2",
        tool_name: "Bash",
        tool_id: "tc-1",
        input: { command: "npm test" },
      });
    });
    await flushBatch();

    expect(result.current.lastActivity).toBe("Implementing fix");
  });

  it("summarizes legacy tool_start events (tool field, not tool_name)", async () => {
    const { result } = renderHook(() => useLiveJobSummary("job-abc"));

    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({
        type: "tool_start",
        timestamp: "t1",
        tool: "Bash",
        input: { command: "npx vitest run" },
      });
    });
    await flushBatch();

    expect(result.current.lastActivity).toBe("tool_call · Bash npx vitest run");
  });

  it("degrades to the bare tool name on malformed tool_call input", async () => {
    const { result } = renderHook(() => useLiveJobSummary("job-abc"));

    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({
        type: "tool_call",
        timestamp: "t1",
        tool_name: "Read",
        tool_id: "tc-1",
        input: null,
      });
    });
    await flushBatch();

    expect(result.current.lastActivity).toBe("tool_call · Read");
  });

  it("truncates over-long tool_call details", async () => {
    const { result } = renderHook(() => useLiveJobSummary("job-abc"));
    const longPath = "a".repeat(100);

    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({
        type: "tool_call",
        timestamp: "t1",
        tool_name: "Edit",
        tool_id: "tc-1",
        input: { file_path: longPath },
      });
    });
    await flushBatch();

    expect(result.current.lastActivity).toBe(`tool_call · Edit ${"a".repeat(80)}…`);
  });

  it("tracks cost fields from cost_update event", async () => {
    const { result } = renderHook(() => useLiveJobSummary("job-abc"));

    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({
        type: "cost_update",
        timestamp: "t1",
        cost_usd: 0.123,
        tokens_in: 500,
        tokens_out: 250,
        num_turns: 7,
      });
    });
    await flushBatch();

    expect(result.current.latestCost).toBe(0.123);
    expect(result.current.latestTokensIn).toBe(500);
    expect(result.current.latestTokensOut).toBe(250);
  });

  it("tracks cost fields and marks isComplete with an immediate flush on complete event", async () => {
    const { result } = renderHook(() => useLiveJobSummary("job-abc"));

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

    // complete triggers an immediate flush without needing to advance timers
    expect(result.current.isComplete).toBe(true);
    expect(result.current.latestCost).toBe(0.042);
    expect(result.current.latestTokensIn).toBe(1000);
    expect(result.current.latestTokensOut).toBe(400);
  });

  it("marks isComplete and flushes pending activity immediately on stream_end", async () => {
    const { result } = renderHook(() => useLiveJobSummary("job-abc"));

    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({ type: "progress", timestamp: "t1", message: "Wrapping up" });
      lastCreatedWs!.simulateMessage({ type: "stream_end", timestamp: "t2" });
    });

    expect(result.current.isComplete).toBe(true);
    expect(result.current.lastActivity).toBe("Wrapping up");
  });

  it("tracks currentNode and attempt from node_started events, accepting both node and node_id fields", async () => {
    const { result } = renderHook(() => useLiveJobSummary("job-abc"));

    await act(async () => {
      lastCreatedWs!.simulateOpen();
      // Stream-writer shape uses `node`; graph-layer shape uses `node_id`
      lastCreatedWs!.simulateMessage({ type: "node_started", timestamp: "t1", node: "triage" });
    });
    await flushBatch();

    expect(result.current.currentNode).toBe("triage");
    expect(result.current.attempt).toBe(1);

    await act(async () => {
      lastCreatedWs!.simulateMessage({ type: "node_started", timestamp: "t2", node_id: "review" });
    });
    await flushBatch();

    expect(result.current.currentNode).toBe("review");
    expect(result.current.attempt).toBe(1);
  });

  it("increments the attempt number when the same node restarts", async () => {
    const { result } = renderHook(() => useLiveJobSummary("job-abc"));

    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({ type: "pipeline_stage_started", timestamp: "t1", node_id: "review" });
      lastCreatedWs!.simulateMessage({ type: "pipeline_stage_started", timestamp: "t2", node_id: "review" });
    });
    await flushBatch();

    expect(result.current.currentNode).toBe("review");
    expect(result.current.attempt).toBe(2);
  });

  it("points currentNode at agent_started without incrementing the attempt count", async () => {
    const { result } = renderHook(() => useLiveJobSummary("job-abc"));

    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({ type: "node_started", timestamp: "t1", node_id: "developer" });
      lastCreatedWs!.simulateMessage({ type: "agent_started", timestamp: "t2", node_id: "developer" });
    });
    await flushBatch();

    expect(result.current.currentNode).toBe("developer");
    expect(result.current.attempt).toBe(1);
  });

  it("preserves the summary across reconnect after close", async () => {
    const { result } = renderHook(() => useLiveJobSummary("job-abc"));
    const initialWsCount = allCreatedWs.length;

    // Initial connection with some events
    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({ type: "node_started", timestamp: "t1", node_id: "developer" });
      lastCreatedWs!.simulateMessage({ type: "progress", timestamp: "t2", message: "Editing files" });
    });
    await flushBatch();

    expect(result.current.lastActivity).toBe("Editing files");
    expect(result.current.currentNode).toBe("developer");

    // Simulate close (triggers reconnect logic)
    await act(async () => {
      lastCreatedWs!.simulateClose();
    });

    // Summary should be preserved (not wiped)
    expect(result.current.lastActivity).toBe("Editing files");
    expect(result.current.currentNode).toBe("developer");
    expect(result.current.isConnected).toBe(false);

    // Advance to trigger reconnect (3000 ms)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    // New socket should be created
    expect(allCreatedWs.length).toBe(initialWsCount + 1);
    const newWs = lastCreatedWs!;

    // Simulate new connection opening and receiving more events
    await act(async () => {
      newWs.simulateOpen();
      newWs.simulateMessage({ type: "progress", timestamp: "t3", message: "Opening PR" });
    });
    await flushBatch();

    expect(result.current.isConnected).toBe(true);
    expect(result.current.lastActivity).toBe("Opening PR");
  });

  it("does not reconnect after stream_end close (isComplete=true)", async () => {
    renderHook(() => useLiveJobSummary("job-abc"));
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
    const { unmount } = renderHook(() => useLiveJobSummary("job-abc"));
    const ws = lastCreatedWs!;

    unmount();

    expect(ws.close).toHaveBeenCalledTimes(1);
  });

  it("loads persisted events via the REST fallback when the WS never connects", async () => {
    vi.mocked(fetchJobLogEvents).mockResolvedValue([
      { type: "node_started", timestamp: "t1", node_id: "developer" },
      { type: "progress", timestamp: "t2", message: "Finished implementation" },
      { type: "cost_update", timestamp: "t3", cost_usd: 0.5, tokens_in: 200, tokens_out: 100, num_turns: 4 },
      { type: "complete", timestamp: "t4", cost_usd: 0.6 },
    ] as never);

    const { result } = renderHook(() => useLiveJobSummary("job-done"));

    // WS never opens; advance past the 1s fallback delay
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1100);
    });

    expect(fetchJobLogEvents).toHaveBeenCalledWith("job-done");
    expect(result.current.lastActivity).toBe("Finished implementation");
    expect(result.current.currentNode).toBe("developer");
    expect(result.current.latestCost).toBe(0.6);
    expect(result.current.latestTokensIn).toBe(200);
    expect(result.current.latestTokensOut).toBe(100);
    expect(result.current.isComplete).toBe(true);
    expect(result.current.isConnected).toBe(false);
  });

  it("skips the REST fallback once the WS has connected", async () => {
    renderHook(() => useLiveJobSummary("job-live"));

    // Open first (flushes the effect that cancels the fallback timer), then
    // advance past the 1s fallback delay
    await act(async () => {
      lastCreatedWs!.simulateOpen();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1100);
    });

    expect(fetchJobLogEvents).not.toHaveBeenCalled();
  });

  it("resets state when jobId changes", async () => {
    const { result, rerender } = renderHook(({ id }: { id: string }) => useLiveJobSummary(id), {
      initialProps: { id: "job-first" },
    });

    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({ type: "node_started", timestamp: "t1", node_id: "review" });
      lastCreatedWs!.simulateMessage({ type: "progress", timestamp: "t2", message: "Reviewing" });
      lastCreatedWs!.simulateMessage({
        type: "cost_update",
        timestamp: "t3",
        cost_usd: 0.5,
        tokens_in: 200,
        tokens_out: 100,
        num_turns: 2,
      });
      lastCreatedWs!.simulateMessage({ type: "stream_end", timestamp: "t4" });
    });

    expect(result.current.isComplete).toBe(true);
    expect(result.current.lastActivity).toBe("Reviewing");
    expect(result.current.latestCost).toBe(0.5);

    // Switch to a new jobId — hook should reset
    await act(async () => {
      rerender({ id: "job-second" });
    });

    expect(result.current.isComplete).toBe(false);
    expect(result.current.lastActivity).toBeNull();
    expect(result.current.latestCost).toBe(0);
    expect(result.current.currentNode).toBeNull();
    expect(result.current.attempt).toBe(1);
    // A new WebSocket should have been created
    expect(allCreatedWs.length).toBe(2);
    expect(lastCreatedWs!.url).toContain("job-second");
  });
});
