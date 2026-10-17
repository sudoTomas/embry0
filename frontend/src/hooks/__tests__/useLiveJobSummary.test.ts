import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";

// RAV-1070: the hook mints a job-scoped WS token before connecting.
vi.mock("@/api/jobs", () => ({
  fetchJobEventsToken: vi.fn().mockResolvedValue({ token: "test-ws-token", expires_in: 900 }),
}));

/** Flush the async token mint so the mock WebSocket exists. */
async function flushConnect() {
  await act(async () => {});
}
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
  // Store an API key so the WS subprotocol is populated (localStorage is the
  // only key source since RAV-1058).
  window.localStorage.setItem("embry0.api-key", "test-api-key");
  vi.useFakeTimers();
  // Default: no persisted events (individual tests override)
  vi.mocked(fetchJobLogEvents).mockReset().mockResolvedValue([]);
});

afterEach(() => {
  vi.useRealTimers();
  window.localStorage.clear();
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
  it("returns empty / disconnected state when jobId is undefined", async () => {
    const { result } = renderHook(() => useLiveJobSummary(undefined));
    await flushConnect();

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

  it("creates a WebSocket with the correct URL and scoped-token subprotocol when jobId is provided", async () => {
    renderHook(() => useLiveJobSummary("job-123"));
    await flushConnect();

    expect(lastCreatedWs).not.toBeNull();
    expect(lastCreatedWs!.url).toBe("ws://localhost:3001/ws/jobs/job-123/events");
    expect(lastCreatedWs!.protocols).toEqual(["embry0.token.test-ws-token"]);
  });

  it("sets isConnected to true when the WebSocket opens", async () => {
    const { result } = renderHook(() => useLiveJobSummary("job-abc"));
    await flushConnect();

    await act(async () => {
      lastCreatedWs!.simulateOpen();
    });

    expect(result.current.isConnected).toBe(true);
  });

  it("surfaces the latest progress event message after the 50 ms flush window", async () => {
    const { result } = renderHook(() => useLiveJobSummary("job-abc"));
    await flushConnect();

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
    await flushConnect();

    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({
        type: "tool_call",
        timestamp: "t1",
        tool_name: "Edit",
        tool_id: "tc-1",
        summary: "apps/recruit/notes.ts",
        tool_input: { file_path: "apps/recruit/notes.ts" },
      });
    });
    await flushBatch();

    expect(result.current.lastActivity).toBe("tool_call · Edit apps/recruit/notes.ts");
  });

  it("prefers the latest progress message over a newer tool_call", async () => {
    const { result } = renderHook(() => useLiveJobSummary("job-abc"));
    await flushConnect();

    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({ type: "progress", timestamp: "t1", message: "Implementing fix" });
      lastCreatedWs!.simulateMessage({
        type: "tool_call",
        timestamp: "t2",
        tool_name: "Bash",
        tool_id: "tc-1",
        summary: "npm test",
        tool_input: { command: "npm test" },
      });
    });
    await flushBatch();

    expect(result.current.lastActivity).toBe("Implementing fix");
  });

  it("degrades to the bare tool name on malformed tool_call input", async () => {
    const { result } = renderHook(() => useLiveJobSummary("job-abc"));
    await flushConnect();

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
    await flushConnect();
    const longPath = "a".repeat(100);

    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({
        type: "tool_call",
        timestamp: "t1",
        tool_name: "Edit",
        tool_id: "tc-1",
        summary: longPath,
        tool_input: { file_path: longPath },
      });
    });
    await flushBatch();

    expect(result.current.lastActivity).toBe(`tool_call · Edit ${"a".repeat(80)}…`);
  });

  it("sums cost_update deltas across runs instead of keeping the last one", async () => {
    const { result } = renderHook(() => useLiveJobSummary("job-abc"));
    await flushConnect();

    const costUpdate = (run: string, cost: number, tin: number, tout: number) => ({
      type: "cost_update", timestamp: "t1", run_id: run, cost_usd: cost, tokens_in: tin, tokens_out: tout,
      num_turns: 1, duration_ms: 1, duration_api_ms: 1, cache_read_tokens: 0, cache_creation_tokens: 0,
    });
    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage(costUpdate("run-1", 0.1, 500, 250));
      lastCreatedWs!.simulateMessage(costUpdate("run-2", 0.2, 100, 50));
    });
    await flushBatch();

    expect(result.current.latestCost).toBeCloseTo(0.3, 6);
    expect(result.current.latestTokensIn).toBe(600);
    expect(result.current.latestTokensOut).toBe(300);
  });

  it("adds turn_usage partials until the run's cost_update lands", async () => {
    const { result } = renderHook(() => useLiveJobSummary("job-abc"));
    await flushConnect();

    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({
        type: "turn_usage", timestamp: "t1", run_id: "run-1", cost_usd: 0.02, input_tokens: 300,
        output_tokens: 30, cache_read_tokens: 0, cache_creation_tokens: 0, model: "m",
      });
    });
    await flushBatch();

    expect(result.current.latestCost).toBeCloseTo(0.02, 6);
    expect(result.current.latestTokensIn).toBe(300);

    await act(async () => {
      lastCreatedWs!.simulateMessage({
        type: "cost_update", timestamp: "t2", run_id: "run-1", cost_usd: 0.05, tokens_in: 600, tokens_out: 60,
        num_turns: 2, duration_ms: 1, duration_api_ms: 1, cache_read_tokens: 0, cache_creation_tokens: 0,
      });
    });
    await flushBatch();

    expect(result.current.latestCost).toBeCloseTo(0.05, 6);
    expect(result.current.latestTokensIn).toBe(600);
    expect(result.current.latestTokensOut).toBe(60);
  });

  it("marks isComplete and flushes pending activity immediately on job_finished", async () => {
    const { result } = renderHook(() => useLiveJobSummary("job-abc"));
    await flushConnect();

    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({ type: "progress", timestamp: "t1", message: "Wrapping up", event_seq: 1 });
      lastCreatedWs!.simulateMessage({
        type: "job_finished",
        timestamp: "t2",
        status: "completed",
        error_code: null,
        error_message: null,
        pr_url: null,
        total_cost_usd: 0,
        event_seq: 2,
      });
    });

    expect(result.current.isComplete).toBe(true);
    expect(result.current.lastActivity).toBe("Wrapping up");
  });

  it("tracks currentNode and attempt from node_started events", async () => {
    const { result } = renderHook(() => useLiveJobSummary("job-abc"));
    await flushConnect();

    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({ type: "node_started", timestamp: "t1", node: "triage" });
    });
    await flushBatch();

    expect(result.current.currentNode).toBe("triage");
    expect(result.current.attempt).toBe(1);

    await act(async () => {
      lastCreatedWs!.simulateMessage({ type: "node_started", timestamp: "t2", node: "review" });
    });
    await flushBatch();

    expect(result.current.currentNode).toBe("review");
    expect(result.current.attempt).toBe(1);
  });

  it("takes the attempt number from node_started", async () => {
    const { result } = renderHook(() => useLiveJobSummary("job-abc"));
    await flushConnect();

    // The stream may join mid-job: the first node_started it sees can be a retry.
    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({ type: "node_started", timestamp: "t1", node: "review", attempt: 2 });
    });
    await flushBatch();

    expect(result.current.currentNode).toBe("review");
    expect(result.current.attempt).toBe(2);
  });

  it("falls back to counting starts when node_started carries no attempt", async () => {
    const { result } = renderHook(() => useLiveJobSummary("job-abc"));
    await flushConnect();

    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({ type: "node_started", timestamp: "t1", node: "review" });
      lastCreatedWs!.simulateMessage({ type: "node_started", timestamp: "t2", node: "review" });
    });
    await flushBatch();

    expect(result.current.currentNode).toBe("review");
    expect(result.current.attempt).toBe(2);
  });

  it("points currentNode at agent_started without incrementing the attempt count", async () => {
    const { result } = renderHook(() => useLiveJobSummary("job-abc"));
    await flushConnect();

    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({ type: "node_started", timestamp: "t1", node: "developer" });
      lastCreatedWs!.simulateMessage({ type: "agent_started", timestamp: "t2", node: "developer" });
    });
    await flushBatch();

    expect(result.current.currentNode).toBe("developer");
    expect(result.current.attempt).toBe(1);
  });

  it("preserves the summary across reconnect after close", async () => {
    const { result } = renderHook(() => useLiveJobSummary("job-abc"));
    await flushConnect();
    const initialWsCount = allCreatedWs.length;

    // Initial connection with some events
    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({ type: "node_started", timestamp: "t1", node: "developer" });
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

  it("does not reconnect after job_finished close (isComplete=true)", async () => {
    renderHook(() => useLiveJobSummary("job-abc"));
    await flushConnect();
    const wsCount = allCreatedWs.length;

    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({
        type: "job_finished",
        timestamp: "t1",
        status: "completed",
        error_code: null,
        error_message: null,
        pr_url: null,
        total_cost_usd: 0,
        event_seq: 1,
      });
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
    await flushConnect();
    const ws = lastCreatedWs!;

    unmount();

    expect(ws.close).toHaveBeenCalledTimes(1);
  });

  it("loads persisted events via the REST fallback when the WS never connects", async () => {
    vi.mocked(fetchJobLogEvents).mockResolvedValue([
      { type: "node_started", timestamp: "t1", node: "developer" },
      { type: "progress", timestamp: "t2", message: "Finished implementation" },
      { type: "cost_update", timestamp: "t3", cost_usd: 0.5, tokens_in: 200, tokens_out: 100, num_turns: 4 },
      {
        type: "job_finished",
        timestamp: "t5",
        status: "completed",
        error_code: null,
        error_message: null,
        pr_url: null,
        total_cost_usd: 0.6,
      },
    ] as never);

    const { result } = renderHook(() => useLiveJobSummary("job-done"));
    await flushConnect();

    // WS never opens; advance past the 1s fallback delay
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1100);
    });

    expect(fetchJobLogEvents).toHaveBeenCalledWith("job-done", 0);
    expect(result.current.lastActivity).toBe("Finished implementation");
    expect(result.current.currentNode).toBe("developer");
    expect(result.current.latestCost).toBe(0.5);
    expect(result.current.latestTokensIn).toBe(200);
    expect(result.current.latestTokensOut).toBe(100);
    expect(result.current.isComplete).toBe(true);
    expect(result.current.isConnected).toBe(false);
  });

  it("skips the REST fallback once the WS has connected", async () => {
    renderHook(() => useLiveJobSummary("job-live"));
    await flushConnect();

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
    await flushConnect();

    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({ type: "node_started", timestamp: "t1", node: "review" });
      lastCreatedWs!.simulateMessage({ type: "progress", timestamp: "t2", message: "Reviewing" });
      lastCreatedWs!.simulateMessage({
        type: "cost_update",
        timestamp: "t3",
        cost_usd: 0.5,
        tokens_in: 200,
        tokens_out: 100,
        num_turns: 2,
      });
      lastCreatedWs!.simulateMessage({
        type: "job_finished",
        timestamp: "t4",
        status: "completed",
        error_code: null,
        error_message: null,
        pr_url: null,
        total_cost_usd: 0.5,
        event_seq: 4,
      });
    });

    expect(result.current.isComplete).toBe(true);
    expect(result.current.lastActivity).toBe("Reviewing");
    expect(result.current.latestCost).toBe(0.5);

    // Switch to a new jobId — hook should reset
    await act(async () => {
      rerender({ id: "job-second" });
    });
    await flushConnect();

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
