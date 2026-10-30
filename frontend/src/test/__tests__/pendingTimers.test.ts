import { describe, expect, it } from "vitest";
import { describePendingTimers, isFrontendSource, isTracked, trackTimers } from "../pendingTimers";

/**
 * A host of its own over the global timers, so a test reads a tracker of
 * its own while the setup's keeps watching the globals underneath.
 */
function ownHost() {
  return {
    setTimeout: globalThis.setTimeout,
    clearTimeout: globalThis.clearTimeout,
    setInterval: globalThis.setInterval,
    clearInterval: globalThis.clearInterval,
  };
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** This file's own stack frame, as Vitest writes it. */
function thisFrame(): string {
  return (new Error().stack ?? "").split("\n")[1] ?? "";
}

const THIS_FILE = /^src\/test\/__tests__\/pendingTimers\.test\.ts:\d+:\d+$/;

describe("trackTimers", () => {
  it("reports a timer this file started and left pending, and clears it", async () => {
    const host = ownHost();
    const tracker = trackTimers(host);
    let fired = false;
    tracker.start();
    host.setTimeout(() => {
      fired = true;
    }, 20);
    const left = tracker.stop();
    expect(left).toEqual([{ kind: "setTimeout", delay: 20, site: expect.stringMatching(THIS_FILE) }]);
    await sleep(40);
    expect(fired, "a reported timer is cleared, so it cannot fire after jsdom is gone").toBe(false);
  });

  it("reports an interval left running, and stops it", async () => {
    const host = ownHost();
    const tracker = trackTimers(host);
    let ticks = 0;
    tracker.start();
    host.setInterval(() => {
      ticks += 1;
    }, 5);
    expect(tracker.stop()).toEqual([{ kind: "setInterval", delay: 5, site: expect.stringMatching(THIS_FILE) }]);
    await sleep(30);
    expect(ticks).toBe(0);
  });

  it("forgets a timer that fired, and timers cleared by handle or by number", async () => {
    const host = ownHost();
    const tracker = trackTimers(host);
    tracker.start();
    host.setTimeout(() => {}, 0);
    host.clearTimeout(host.setTimeout(() => {}, 1000));
    host.clearTimeout(Number(host.setTimeout(() => {}, 1000)));
    host.clearInterval(host.setInterval(() => {}, 1000));
    await sleep(10);
    expect(tracker.stop()).toEqual([]);
  });

  it("records nothing started outside a test", () => {
    const host = ownHost();
    const tracker = trackTimers(host);
    const early = host.setTimeout(() => {}, 1000);
    tracker.start();
    expect(tracker.stop()).toEqual([]);
    host.clearTimeout(early);
  });

  it("leaves alone, and keeps running, a timer the frontend's own code did not start", async () => {
    const host = ownHost();
    const tracker = trackTimers(host, (frame) => !frame.includes("fromALibrary"));
    let fired = false;
    function fromALibrary() {
      host.setTimeout(() => {
        fired = true;
      }, 10);
    }
    tracker.start();
    fromALibrary();
    expect(tracker.stop()).toEqual([]);
    await sleep(30);
    expect(fired).toBe(true);
  });

  it("still runs a timer's callback with its arguments", async () => {
    const host = ownHost();
    trackTimers(host);
    const got = await new Promise((resolve) => host.setTimeout(resolve, 0, "an argument"));
    expect(got).toBe("an argument");
  });
});

describe("isFrontendSource", () => {
  it("knows a frame in the frontend's source, as Vitest writes it", () => {
    const frame = thisFrame();
    expect(frame).toContain("pendingTimers.test.ts");
    expect(isFrontendSource(frame)).toBe(true);
  });

  it("does not count a dependency or a file outside the frontend", () => {
    const root = /\(?(\/[^\s()]+?\/)src\//.exec(thisFrame())?.[1];
    expect(root, "the frame names a path under the frontend").toBeDefined();
    expect(isFrontendSource(`    at schedule (${root}node_modules/@tanstack/query-core/build/modern/removable.js:12:5)`)).toBe(false);
    expect(isFrontendSource("    at listOnTimeout (node:internal/timers:585:17)")).toBe(false);
    expect(isFrontendSource("    at run (/elsewhere/app/src/main.ts:1:1)")).toBe(false);
  });
});

describe("the test setup", () => {
  it("tracks the timers the frontend starts", () => {
    expect([globalThis.setTimeout, globalThis.clearTimeout, globalThis.setInterval, globalThis.clearInterval].map(isTracked)).toEqual([
      true,
      true,
      true,
      true,
    ]);
  });
});

describe("describePendingTimers", () => {
  it("names each timer, where it started and what to do", () => {
    const text = describePendingTimers([
      { kind: "setTimeout", delay: 150, site: "src/components/issues/RepoSelector.tsx:35:11" },
      { kind: "setInterval", delay: 1000, site: "src/hooks/useLiveDuration.ts:13:19" },
    ]);
    expect(text).toContain("left 2 timers running");
    expect(text).toContain("setTimeout 150 ms, started at src/components/issues/RepoSelector.tsx:35:11");
    expect(text).toContain("setInterval 1000 ms, started at src/hooks/useLiveDuration.ts:13:19");
    expect(text).toContain("window is not defined");
    expect(text).toContain("useComponentTimeout");
  });

  it("speaks of one timer as one", () => {
    expect(describePendingTimers([{ kind: "setTimeout", delay: 0, site: "src/lib/focus-trap.ts:48:5" }])).toContain("left a timer running");
  });
});
