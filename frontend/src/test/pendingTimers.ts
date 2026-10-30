/**
 * The timers a test leaves behind.
 *
 * Vitest tears jsdom down after a file's last test. A timer still pending
 * then fires later in the same worker, with `window` gone, and a React
 * state update in its callback reads `window.event` and throws
 * "ReferenceError: window is not defined". No test is running by then, so
 * Vitest can only report an unhandled error against the file, and only on a
 * run whose worker lives long enough for the timer to fire (#256).
 *
 * trackTimers wraps a host's setTimeout and setInterval to record each timer
 * the frontend's own code starts while a test runs. The test setup checks
 * them once the test has unmounted what it rendered, so the test that left
 * one running fails every time, naming the line that started it. Timers a
 * library starts (react-query's cache collection, toasts, sockets) are left
 * alone: they update no component, and they are not ours to clear.
 */

export interface PendingTimer {
  kind: "setTimeout" | "setInterval";
  delay: number;
  /** Where the timer was started, as `path:line:column` from the frontend root. */
  site: string;
}

export interface TimerTracker {
  /** Start recording timers, at the start of a test. */
  start(): void;
  /** Stop recording, clear every recorded timer the frontend started that is still pending, and return those. */
  stop(): PendingTimer[];
}

type TimerHandle = ReturnType<typeof setTimeout>;
type TimerHost = Pick<typeof globalThis, "setTimeout" | "clearTimeout" | "setInterval" | "clearInterval">;

interface Recorded {
  kind: PendingTimer["kind"];
  delay: number;
  /** Captured when the timer starts; its stack is read only if the timer is still pending at the end. */
  origin: Error;
}

const TRACKED = Symbol.for("embry0.pendingTimers.tracked");
const MODULE_PATH = decodeURIComponent(new URL(import.meta.url).pathname);
// Cut from this module's own path. A relative `new URL(path, import.meta.url)`
// would not do: Vite rewrites that form into an asset URL (/@fs/...), and no
// stack frame would ever match it.
const FRONTEND_ROOT = MODULE_PATH.slice(0, MODULE_PATH.lastIndexOf("/src/") + 1);
const SOURCE_ROOT = `${FRONTEND_ROOT}src/`;

/** Whether a stack frame is in the frontend's own source, not a dependency. */
export function isFrontendSource(frame: string): boolean {
  return frame.includes(SOURCE_ROOT) && !frame.includes("/node_modules/");
}

/** Whether `fn` is one of the wrappers trackTimers installs. */
export function isTracked(fn: unknown): boolean {
  return typeof fn === "function" && TRACKED in fn;
}

/** The frame that called the wrapper: the first one outside this module. */
function callerFrame(origin: Error): string {
  const frames = (origin.stack ?? "").split("\n").slice(1);
  return frames.find((frame) => !frame.includes(MODULE_PATH))?.trim() ?? "";
}

/** `path:line:column` of a stack frame, relative to the frontend root. */
function siteOf(frame: string): string {
  const location = /\(?((?:file:\/\/)?[^\s()]+:\d+:\d+)\)?$/.exec(frame)?.[1] ?? frame;
  const path = location.replace(/^file:\/\//, "");
  return path.startsWith(FRONTEND_ROOT) ? path.slice(FRONTEND_ROOT.length) : path;
}

function mark<F extends object>(fn: F): F {
  return Object.assign(fn, { [TRACKED]: true });
}

/**
 * Wrap `host`'s timer functions, once, and return the tracker that reads
 * them. `isOwnCode` decides, from the frame that started a timer, whether
 * the timer is the frontend's to answer for.
 */
export function trackTimers(host: TimerHost, isOwnCode: (frame: string) => boolean = isFrontendSource): TimerTracker {
  const real = {
    setTimeout: host.setTimeout,
    clearTimeout: host.clearTimeout,
    setInterval: host.setInterval,
    clearInterval: host.clearInterval,
  };
  let recording = false;
  const pending = new Map<TimerHandle, Recorded>();

  // A browser hands back a number and some code passes that back; Node's
  // handle converts to the same number.
  const forget = (id: unknown) => {
    if (pending.delete(id as TimerHandle) || (typeof id !== "number" && typeof id !== "string")) return;
    for (const handle of pending.keys()) {
      if (Number(handle) === Number(id)) pending.delete(handle);
    }
  };

  const wrappedSetTimeout = (handler: TimerHandler, delay?: number, ...args: unknown[]): TimerHandle => {
    if (typeof handler !== "function") return real.setTimeout(handler, delay, ...args) as TimerHandle;
    const origin = recording ? new Error() : null;
    const handle = real.setTimeout(() => {
      pending.delete(handle);
      handler(...args);
    }, delay) as TimerHandle;
    if (origin) pending.set(handle, { kind: "setTimeout", delay: delay ?? 0, origin });
    return handle;
  };
  const wrappedSetInterval = (handler: TimerHandler, delay?: number, ...args: unknown[]): TimerHandle => {
    const handle = real.setInterval(handler, delay, ...args) as TimerHandle;
    if (recording && typeof handler === "function") {
      pending.set(handle, { kind: "setInterval", delay: delay ?? 0, origin: new Error() });
    }
    return handle;
  };
  const wrappedClearTimeout = (id?: TimerHandle | number | string) => {
    forget(id);
    real.clearTimeout(id as TimerHandle);
  };
  const wrappedClearInterval = (id?: TimerHandle | number | string) => {
    forget(id);
    real.clearInterval(id as TimerHandle);
  };

  Object.assign(host, {
    setTimeout: mark(Object.assign(wrappedSetTimeout, real.setTimeout)),
    setInterval: mark(Object.assign(wrappedSetInterval, real.setInterval)),
    clearTimeout: mark(wrappedClearTimeout),
    clearInterval: mark(wrappedClearInterval),
  });

  return {
    start() {
      pending.clear();
      recording = true;
    },
    stop() {
      recording = false;
      const left: PendingTimer[] = [];
      for (const [handle, timer] of pending) {
        const frame = callerFrame(timer.origin);
        if (!isOwnCode(frame)) continue;
        (timer.kind === "setTimeout" ? real.clearTimeout : real.clearInterval)(handle);
        left.push({ kind: timer.kind, delay: timer.delay, site: siteOf(frame) });
      }
      pending.clear();
      return left;
    },
  };
}

/** Why a test failed for the timers it left running, and what to do about it. */
export function describePendingTimers(timers: PendingTimer[]): string {
  const lines = timers.map((t) => `  ${t.kind} ${t.delay} ms, started at ${t.site}`);
  return [
    `This test left ${timers.length === 1 ? "a timer" : `${timers.length} timers`} running after unmounting what it rendered:`,
    ...lines,
    "A timer that outlives its test can fire after Vitest tears jsdom down, where a React state update throws " +
      '"ReferenceError: window is not defined" and fails the run with no test to blame (#256). ' +
      "Clear the timer when its component unmounts (useComponentTimeout), or have the test wait for it.",
  ].join("\n");
}
