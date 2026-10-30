import "@testing-library/jest-dom/vitest";
import { beforeEach } from "vitest";
import { describePendingTimers, trackTimers } from "./test/pendingTimers";

// jsdom lacks ResizeObserver / DOMMatrixReadOnly, which @xyflow/react reaches
// for on mount. Polyfill them with no-op stubs so any test that mounts a
// ReactFlow canvas doesn't crash on first render.
if (typeof globalThis.ResizeObserver === "undefined") {
  class ResizeObserverStub {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  }
  globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;
}

if (typeof globalThis.DOMMatrixReadOnly === "undefined") {
  class DOMMatrixReadOnlyStub {
    m22 = 1;
    constructor(_init?: string | number[]) {}
  }
  globalThis.DOMMatrixReadOnly =
    DOMMatrixReadOnlyStub as unknown as typeof DOMMatrixReadOnly;
}

// Fail a test that leaves a timer of ours running after what it rendered is
// unmounted: such a timer can fire after jsdom is torn down and fail the run
// with no test to blame (#256; src/test/pendingTimers.ts). The check runs
// when the test has finished, after every afterEach hook, Testing Library's
// cleanup among them. It does not import Testing Library itself: that would
// load react-dom before a test file's vi.hoisted polyfills. A test that has
// already failed is not failed again for timers its failure left behind.
const timers = trackTimers(globalThis);
beforeEach(({ onTestFinished }) => {
  timers.start();
  onTestFinished(({ task }) => {
    const left = timers.stop();
    if (left.length > 0 && task.result?.state !== "fail") {
      throw new Error(describePendingTimers(left));
    }
  });
});
