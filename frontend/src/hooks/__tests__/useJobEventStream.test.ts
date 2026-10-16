import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";

vi.mock("@/api/jobs", () => ({
  fetchJobEventsToken: vi.fn().mockResolvedValue({ token: "test-ws-token", expires_in: 900 }),
}));
vi.mock("@/api/logs", () => ({
  fetchJobLogEvents: vi.fn(),
}));

import { useJobEventStream, type JobEventStreamHandlers } from "../useJobEventStream";
import { fetchJobLogEvents } from "@/api/logs";

interface MockWebSocketInstance {
  url: string;
  onopen: ((event: Event) => void) | null;
  onmessage: ((event: MessageEvent) => void) | null;
  onclose: ((event: CloseEvent) => void) | null;
  onerror: ((event: Event) => void) | null;
  close: ReturnType<typeof vi.fn>;
  simulateOpen(): void;
  simulateMessage(data: object): void;
  simulateClose(code?: number): void;
}

let lastCreatedWs: MockWebSocketInstance | null = null;
const allCreatedWs: MockWebSocketInstance[] = [];

class MockWebSocket implements MockWebSocketInstance {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  url: string;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  close = vi.fn();
  constructor(url: string) {
    this.url = url;
    // eslint-disable-next-line @typescript-eslint/no-this-alias
    lastCreatedWs = this;
    allCreatedWs.push(this);
  }
  simulateOpen() {
    this.onopen?.(new Event("open"));
  }
  simulateMessage(data: object) {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(data) }));
  }
  simulateClose(code = 1006) {
    this.onclose?.(new CloseEvent("close", { code }));
  }
}

async function flushConnect() {
  await act(async () => {});
}

function harness(jobId = "job-1") {
  const onEvent = vi.fn();
  const onStreamEnd = vi.fn();
  const onFallbackEvents = vi.fn();
  const handlers: JobEventStreamHandlers = { onEvent, onStreamEnd, onFallbackEvents };
  const hook = renderHook(() => useJobEventStream(jobId, handlers, false));
  return { ...hook, onEvent, onStreamEnd, onFallbackEvents };
}

beforeEach(() => {
  lastCreatedWs = null;
  allCreatedWs.length = 0;
  vi.stubGlobal("WebSocket", MockWebSocket);
  Object.defineProperty(window, "location", {
    value: { protocol: "http:", host: "localhost:3001" },
    writable: true,
    configurable: true,
  });
  vi.useFakeTimers();
  vi.mocked(fetchJobLogEvents).mockReset().mockResolvedValue([]);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("useJobEventStream replay cursor (spec 3.5, #38)", () => {
  it("connects without a cursor first and reconnects with ?since_seq=<last seen>", async () => {
    harness();
    await flushConnect();
    expect(lastCreatedWs!.url).toBe("ws://localhost:3001/ws/jobs/job-1/events");
    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({ type: "progress", timestamp: "t1", message: "a", event_seq: 11 });
      lastCreatedWs!.simulateMessage({ type: "progress", timestamp: "t2", message: "b", event_seq: 12 });
      lastCreatedWs!.simulateClose();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(allCreatedWs).toHaveLength(2);
    expect(lastCreatedWs!.url).toBe("ws://localhost:3001/ws/jobs/job-1/events?since_seq=12");
  });

  it("drops a replayed event whose seq it already processed", async () => {
    const { onEvent } = harness();
    await flushConnect();
    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({ type: "progress", timestamp: "t1", message: "a", event_seq: 5 });
      lastCreatedWs!.simulateMessage({ type: "progress", timestamp: "t1", message: "a", event_seq: 5 });
      lastCreatedWs!.simulateMessage({ type: "progress", timestamp: "t0", message: "old", event_seq: 4 });
    });
    expect(onEvent).toHaveBeenCalledTimes(1);
  });

  it("an interleaved seq (2 then 5 with prev_seq 2) fetches nothing and delivers both", async () => {
    // event_seq is job_logs.id, shared by every job: 3 and 4 belong to other jobs.
    const { onEvent } = harness();
    await flushConnect();
    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({ type: "progress", timestamp: "t2", message: "two", event_seq: 2 });
      lastCreatedWs!.simulateMessage({ type: "progress", timestamp: "t5", message: "five", event_seq: 5, prev_seq: 2 });
    });
    await act(async () => {});
    expect(fetchJobLogEvents, "no REST fetch for ids another job used").not.toHaveBeenCalled();
    const seqs = onEvent.mock.calls.map((c) => (c[0] as { event_seq: number }).event_seq);
    expect(seqs).toEqual([2, 5]);
  });

  it("a real gap (2 then 5 with prev_seq 4) fetches from the cursor and delivers in order", async () => {
    vi.mocked(fetchJobLogEvents).mockResolvedValue([
      { type: "progress", timestamp: "t3", message: "three", event_seq: 3, prev_seq: 2 },
      { type: "progress", timestamp: "t4", message: "four", event_seq: 4, prev_seq: 3 },
      { type: "progress", timestamp: "t5", message: "five", event_seq: 5, prev_seq: 4 },
    ] as never);
    const { onEvent } = harness();
    await flushConnect();
    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({ type: "progress", timestamp: "t2", message: "two", event_seq: 2 });
      lastCreatedWs!.simulateMessage({ type: "progress", timestamp: "t5", message: "five", event_seq: 5, prev_seq: 4 });
      // arrives while the fill is in flight: must wait behind it
      lastCreatedWs!.simulateMessage({ type: "progress", timestamp: "t6", message: "six", event_seq: 6, prev_seq: 5 });
    });
    await act(async () => {});
    expect(fetchJobLogEvents).toHaveBeenCalledWith("job-1", 2);
    const seqs = onEvent.mock.calls.map((c) => (c[0] as { event_seq: number }).event_seq);
    expect(seqs).toEqual([2, 3, 4, 5, 6]);
  });

  it("a reconnect during a fill does not queue the new connection behind it", async () => {
    vi.mocked(fetchJobLogEvents).mockReturnValueOnce(new Promise(() => {})); // a fill that never lands
    const { onEvent } = harness();
    await flushConnect();
    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({ type: "progress", timestamp: "t1", message: "one", event_seq: 1 });
      lastCreatedWs!.simulateMessage({ type: "progress", timestamp: "t3", message: "three", event_seq: 3, prev_seq: 2 });
      lastCreatedWs!.simulateClose();
    });
    expect(fetchJobLogEvents).toHaveBeenCalledWith("job-1", 1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(allCreatedWs).toHaveLength(2);
    onEvent.mockClear();
    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({ type: "progress", timestamp: "t2", message: "two", event_seq: 2, prev_seq: 1 });
    });
    expect(onEvent, "the replay on the new socket must not wait behind the old fill").toHaveBeenCalledTimes(1);
  });

  it("drops a gap fill that lands after the job changed", async () => {
    let resolveFill: (events: never) => void = () => {};
    vi.mocked(fetchJobLogEvents).mockReturnValueOnce(
      new Promise((resolve) => {
        resolveFill = resolve as (events: never) => void;
      }),
    );
    const onEvent = vi.fn();
    const { rerender } = renderHook(({ id }: { id: string }) => useJobEventStream(id, { onEvent }, false), {
      initialProps: { id: "job-1" },
    });
    await flushConnect();
    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({ type: "progress", timestamp: "t1", message: "one", event_seq: 1 });
      lastCreatedWs!.simulateMessage({ type: "progress", timestamp: "t3", message: "three", event_seq: 3, prev_seq: 2 });
    });
    expect(fetchJobLogEvents).toHaveBeenCalledWith("job-1", 1);
    await act(async () => {
      rerender({ id: "job-2" });
    });
    onEvent.mockClear();
    await act(async () => {
      resolveFill([{ type: "progress", timestamp: "t2", message: "two", event_seq: 2 }] as never);
    });
    expect(onEvent).not.toHaveBeenCalled();
  });

  it("does not treat the first event of a fresh connection as a gap", async () => {
    const { onEvent } = harness();
    await flushConnect();
    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({ type: "progress", timestamp: "t9", message: "nine", event_seq: 9 });
    });
    expect(fetchJobLogEvents).not.toHaveBeenCalled();
    expect(onEvent).toHaveBeenCalledTimes(1);
  });

  it("marks the stream complete on job_finished and never reconnects", async () => {
    const { result, onEvent, onStreamEnd } = harness();
    await flushConnect();
    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({
        type: "job_finished", timestamp: "t1", status: "completed", error_code: null, error_message: null, pr_url: null, total_cost_usd: 0.4, event_seq: 20,
      });
      lastCreatedWs!.simulateClose(1000);
    });
    expect(onEvent).toHaveBeenCalledTimes(1);
    expect(onStreamEnd).toHaveBeenCalledTimes(1);
    expect(result.current.isComplete).toBe(true);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });
    expect(allCreatedWs).toHaveLength(1);
  });

  it("treats a server close with code 1000 as complete even without job_finished", async () => {
    const { result } = harness();
    await flushConnect();
    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({ type: "progress", timestamp: "t1", message: "a", event_seq: 1 });
      lastCreatedWs!.simulateClose(1000);
    });
    expect(result.current.isComplete).toBe(true);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });
    expect(allCreatedWs).toHaveLength(1);
  });

  it("marks complete from a persisted job_finished on the REST fallback", async () => {
    vi.mocked(fetchJobLogEvents).mockResolvedValue([
      { type: "progress", timestamp: "t1", message: "a", event_seq: 1 },
      { type: "job_finished", timestamp: "t2", status: "failed", error_code: "ERR_X", error_message: "x", pr_url: null, total_cost_usd: 0, event_seq: 2 },
    ] as never);
    const { result, onFallbackEvents } = harness("job-done");
    await flushConnect();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1100);
    });
    expect(fetchJobLogEvents).toHaveBeenCalledWith("job-done", 0);
    expect(onFallbackEvents).toHaveBeenCalledTimes(1);
    expect(result.current.isComplete).toBe(true);
  });
});
