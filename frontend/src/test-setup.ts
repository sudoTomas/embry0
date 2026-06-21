import "@testing-library/jest-dom/vitest";

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
