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
import { useJobLogs } from "../useJobLogs";
import { useAgentStates } from "../useAgentStates";
import { fetchJobLogEvents } from "@/api/logs";

// Mock the REST fallback so tests control what persisted events exist
vi.mock("@/api/logs", () => ({
  fetchJobLogEvents: vi.fn().mockResolvedValue([]),
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

describe("useJobLogs", () => {
  it("returns empty / disconnected state when jobId is undefined", async () => {
    const { result } = renderHook(() => useJobLogs(undefined));
    await flushConnect();

    expect(result.current.events).toEqual([]);
    expect(result.current.isConnected).toBe(false);
    expect(result.current.isComplete).toBe(false);
    expect(result.current.latestCost).toBe(0);
    expect(result.current.latestTokensIn).toBe(0);
    expect(result.current.latestTokensOut).toBe(0);
    expect(result.current.latestTurns).toBe(0);
    expect(lastCreatedWs).toBeNull();
  });

  it("creates a WebSocket with the correct URL and scoped-token subprotocol when jobId is provided", async () => {
    renderHook(() => useJobLogs("job-123"));
    await flushConnect();

    expect(lastCreatedWs).not.toBeNull();
    expect(lastCreatedWs!.url).toBe("ws://localhost:3001/ws/jobs/job-123/events");
    expect(lastCreatedWs!.protocols).toEqual(["embry0.token.test-ws-token"]);
  });

  it("sets isConnected to true when the WebSocket opens", async () => {
    const { result } = renderHook(() => useJobLogs("job-abc"));
    await flushConnect();

    await act(async () => {
      lastCreatedWs!.simulateOpen();
    });

    expect(result.current.isConnected).toBe(true);
  });

  it("accumulates events after the 50 ms flush window", async () => {
    const { result } = renderHook(() => useJobLogs("job-abc"));
    await flushConnect();

    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({ type: "text", timestamp: "t1", text: "hello" });
      lastCreatedWs!.simulateMessage({ type: "text", timestamp: "t2", text: "world" });
    });

    // Events not visible yet (batch pending)
    expect(result.current.events).toHaveLength(0);

    await flushBatch();

    expect(result.current.events).toHaveLength(2);
    expect((result.current.events[0] as { text: string }).text).toBe("hello");
    expect((result.current.events[1] as { text: string }).text).toBe("world");
  });

  it("marks isComplete and flushes events immediately on job_finished", async () => {
    const { result } = renderHook(() => useJobLogs("job-abc"));
    await flushConnect();

    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({ type: "text", timestamp: "t1", text: "partial", event_seq: 1 });
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

    // job_finished triggers an immediate flush without needing to advance timers
    expect(result.current.isComplete).toBe(true);
    expect(result.current.events).toHaveLength(2);
    expect((result.current.events[0] as { text: string }).text).toBe("partial");
    expect(result.current.events[1].type).toBe("job_finished");
  });

  it("marks isComplete and flushes a cost_update immediately when job_finished follows", async () => {
    const { result } = renderHook(() => useJobLogs("job-abc"));
    await flushConnect();

    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({
        type: "cost_update",
        timestamp: "t1",
        cost_usd: 0.05,
        duration_ms: 1000,
        duration_api_ms: 800,
        num_turns: 3,
        tokens_in: 100,
        tokens_out: 200,
        cache_read_tokens: 0,
        cache_creation_tokens: 0,
        event_seq: 1,
      });
      lastCreatedWs!.simulateMessage({
        type: "job_finished",
        timestamp: "t2",
        status: "completed",
        error_code: null,
        error_message: null,
        pr_url: null,
        total_cost_usd: 0.05,
        event_seq: 2,
      });
    });

    expect(result.current.isComplete).toBe(true);
    expect(result.current.events).toHaveLength(2);
    expect(result.current.events[0].type).toBe("cost_update");
    expect(result.current.events[1].type).toBe("job_finished");
  });

  it("preserves accumulated events across reconnect after close", async () => {
    const { result } = renderHook(() => useJobLogs("job-abc"));
    await flushConnect();
    const initialWsCount = allCreatedWs.length;

    // Initial connection with some events
    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({ type: "text", timestamp: "t1", text: "msg1", event_seq: 1 });
      lastCreatedWs!.simulateMessage({ type: "text", timestamp: "t2", text: "msg2", event_seq: 2 });
    });
    await flushBatch();

    expect(result.current.events).toHaveLength(2);

    // Simulate close (triggers reconnect logic)
    await act(async () => {
      lastCreatedWs!.simulateClose();
    });

    // Events should be preserved (not wiped)
    expect(result.current.events).toHaveLength(2);
    expect(result.current.isConnected).toBe(false);

    // Advance to trigger reconnect (3000 ms)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    // New socket should be created
    expect(allCreatedWs.length).toBe(initialWsCount + 1);
    const newWs = lastCreatedWs!;
    expect(newWs.url).toBe("ws://localhost:3001/ws/jobs/job-abc/events?since_seq=2");

    // Simulate new connection opening and receiving more events
    await act(async () => {
      newWs.simulateOpen();
      newWs.simulateMessage({ type: "text", timestamp: "t3", text: "msg3", event_seq: 3 });
    });
    await flushBatch();

    // Events should accumulate: original 2 + 1 new = 3 total
    expect(result.current.events).toHaveLength(3);
    expect((result.current.events[0] as { text: string }).text).toBe("msg1");
    expect((result.current.events[1] as { text: string }).text).toBe("msg2");
    expect((result.current.events[2] as { text: string }).text).toBe("msg3");
  });

  it("does not reconnect after job_finished close (isComplete=true)", async () => {
    renderHook(() => useJobLogs("job-abc"));
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
    const { unmount } = renderHook(() => useJobLogs("job-abc"));
    await flushConnect();
    const ws = lastCreatedWs!;

    unmount();

    expect(ws.close).toHaveBeenCalledTimes(1);
  });

  it("does not create a WebSocket when unmounted immediately without a jobId", async () => {
    const { unmount } = renderHook(() => useJobLogs(undefined));
    await flushConnect();
    unmount();
    expect(lastCreatedWs).toBeNull();
  });

  it("sums cost_update deltas across runs instead of keeping the last one", async () => {
    const { result } = renderHook(() => useJobLogs("job-abc"));
    await flushConnect();

    const costUpdate = (run: string, cost: number, tin: number, tout: number) => ({
      type: "cost_update", timestamp: "t1", run_id: run, cost_usd: cost, tokens_in: tin, tokens_out: tout,
      num_turns: 1, duration_ms: 1, duration_api_ms: 1, cache_read_tokens: 0, cache_creation_tokens: 0,
    });
    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage(costUpdate("run-1", 0.1, 500, 250));
      lastCreatedWs!.simulateMessage(costUpdate("run-2", 0.2, 100, 50));
      // cost_update is a delta, so a second one for run-1 adds to it
      lastCreatedWs!.simulateMessage(costUpdate("run-1", 0.05, 10, 5));
    });
    await flushBatch();

    expect(result.current.latestCost).toBeCloseTo(0.35, 6);
    expect(result.current.latestTokensIn).toBe(610);
    expect(result.current.latestTokensOut).toBe(305);
  });

  it("counts a run's cost_update once however often its persisted row is replayed (#104)", async () => {
    const { result } = renderHook(() => useJobLogs("job-0001"));
    await flushConnect();

    const row = (seq: number, fields: Record<string, unknown>) => ({ timestamp: "t", event_seq: seq, prev_seq: seq - 1, ...fields });
    const started = row(11, { type: "agent_started", node: "qa", agent: "qa", run_id: "run-0001", model: "m", max_turns: 10 });
    const cost = row(12, {
      type: "cost_update", node: "qa", run_id: "run-0001", cost_usd: 1.602263, num_turns: 57, duration_ms: 1, duration_api_ms: 1,
      tokens_in: 108, tokens_out: 13785, cache_read_tokens: 0, cache_creation_tokens: 0,
    });
    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage(started);
      lastCreatedWs!.simulateMessage(cost);
      // The same persisted row again on this socket (a bus duplicate).
      lastCreatedWs!.simulateMessage(cost);
      lastCreatedWs!.simulateClose();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    // The reconnect replays the whole log, cursor or not.
    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage(started);
      lastCreatedWs!.simulateMessage(cost);
    });
    await flushBatch();

    expect(result.current.events.filter((e) => e.type === "cost_update"), "a replayed row reaches the page once").toHaveLength(1);
    expect(result.current.latestCost, "the HUD total counts the replayed cost_update once").toBeCloseTo(1.602263, 6);
    const { agents } = renderHook(() => useAgentStates(result.current.events, "running", null)).result.current;
    expect(agents.map((a) => [a.runId, a.costUsd]), "one card, its cost once").toEqual([["run-0001", 1.602263]]);
  });

  it("adds turn_usage partials until the run's cost_update lands", async () => {
    const { result } = renderHook(() => useJobLogs("job-abc"));
    await flushConnect();

    const usage = { type: "turn_usage", timestamp: "t1", run_id: "run-1", cost_usd: 0.02, input_tokens: 300,
      output_tokens: 30, cache_read_tokens: 0, cache_creation_tokens: 0, model: "m" };
    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage(usage);
      lastCreatedWs!.simulateMessage({ ...usage, cost_usd: null, input_tokens: 200, output_tokens: 20 });
    });
    await flushBatch();

    expect(result.current.latestCost).toBeCloseTo(0.02, 6);
    expect(result.current.latestTokensIn).toBe(500);
    expect(result.current.latestTokensOut).toBe(50);

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

  it("latestTurns counts top-level turn_start per run", async () => {
    const { result } = renderHook(() => useJobLogs("job-abc"));
    await flushConnect();

    const turn = (run: string, n: number, extra: Record<string, unknown> = {}) =>
      ({ type: "turn_start", timestamp: "t1", run_id: run, turn_number: n, model: "m", ...extra });
    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage(turn("run-1", 1));
      lastCreatedWs!.simulateMessage(turn("run-1", 2));
      lastCreatedWs!.simulateMessage(turn("run-1", 7, { parent_tool_use_id: "toolu-parent" }));
      lastCreatedWs!.simulateMessage(turn("run-2", 1));
    });
    await flushBatch();

    expect(result.current.latestTurns).toBe(3);
  });

  it("sums totals over the REST fallback events when the WS never connects", async () => {
    vi.mocked(fetchJobLogEvents).mockResolvedValue([
      { type: "cost_update", timestamp: "t1", run_id: "run-1", cost_usd: 0.1, tokens_in: 100, tokens_out: 10,
        num_turns: 1, duration_ms: 1, duration_api_ms: 1, cache_read_tokens: 0, cache_creation_tokens: 0 },
      { type: "turn_start", timestamp: "t2", run_id: "run-2", turn_number: 1, model: "m" },
      { type: "cost_update", timestamp: "t3", run_id: "run-2", cost_usd: 0.2, tokens_in: 200, tokens_out: 20,
        num_turns: 1, duration_ms: 1, duration_api_ms: 1, cache_read_tokens: 0, cache_creation_tokens: 0 },
    ] as never);

    const { result } = renderHook(() => useJobLogs("job-done"));
    await flushConnect();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1100);
    });

    expect(fetchJobLogEvents).toHaveBeenCalledWith("job-done", 0);
    expect(result.current.events).toHaveLength(3);
    expect(result.current.latestCost).toBeCloseTo(0.3, 6);
    expect(result.current.latestTokensIn).toBe(300);
    expect(result.current.latestTokensOut).toBe(30);
    expect(result.current.latestTurns).toBe(1);
  });

  it("does not update cost for non-cost event types", async () => {
    const { result } = renderHook(() => useJobLogs("job-abc"));
    await flushConnect();

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
    await flushConnect();

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
    await flushConnect();
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
    await flushConnect();

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
      lastCreatedWs!.simulateMessage({
        type: "job_finished",
        timestamp: "t2",
        status: "completed",
        error_code: null,
        error_message: null,
        pr_url: null,
        total_cost_usd: 0.5,
        event_seq: 2,
      });
    });

    await flushBatch();

    expect(result.current.isComplete).toBe(true);
    expect(result.current.latestCost).toBe(0.5);

    // Switch to a new jobId — hook should reset
    await act(async () => {
      rerender({ id: "job-second" });
    });
    await flushConnect();

    expect(result.current.isComplete).toBe(false);
    expect(result.current.latestCost).toBe(0);
    expect(result.current.events).toHaveLength(0);
    // A new WebSocket should have been created
    expect(allCreatedWs.length).toBe(2);
    expect(lastCreatedWs!.url).toContain("job-second");
  });
});
