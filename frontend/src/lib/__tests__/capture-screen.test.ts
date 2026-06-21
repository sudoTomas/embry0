import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { captureScreen } from "../capture-screen";

// capture-screen wraps `navigator.mediaDevices.getDisplayMedia` and renders the
// first decoded frame to a PNG Blob. Prior assays found two race conditions in
// an earlier draft:
//   (1) using `loadedmetadata` (which fires before any frame is decoded) —
//       could yield a blank PNG;
//   (2) setting `video.srcObject = stream` BEFORE registering the readiness
//       listener — a fast `loadeddata` event could fire before the listener
//       was attached, hanging capture forever.
// These tests pin the contract: listener attached first, `loadeddata` is the
// readiness signal, and the MediaStream tracks are stopped after capture so
// the browser's recording indicator disappears.

interface FakeTrack {
  stop: ReturnType<typeof vi.fn>;
}
interface FakeStream {
  getTracks: () => FakeTrack[];
}

let stoppedCount = 0;
let listenerAttachedAt = -1;
let srcObjectSetAt = -1;
let stepCounter = 0;
const readinessEvents: string[] = [];

beforeEach(() => {
  stoppedCount = 0;
  listenerAttachedAt = -1;
  srcObjectSetAt = -1;
  stepCounter = 0;
  readinessEvents.length = 0;

  // Minimal getDisplayMedia stub — returns a stream with one trackable track.
  const track: FakeTrack = { stop: vi.fn(() => void stoppedCount++) };
  const stream: FakeStream = { getTracks: () => [track] };
  (globalThis.navigator as unknown as {
    mediaDevices: { getDisplayMedia: () => Promise<FakeStream> };
  }).mediaDevices = {
    getDisplayMedia: vi.fn().mockResolvedValue(stream),
  };

  // Wrap createElement so we can instrument the HTMLVideoElement used by
  // captureScreen. We only intercept `video` — everything else falls through.
  const realCreate = document.createElement.bind(document);
  vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
    if (tag === "video") {
      const listeners: Record<string, Array<EventListener>> = {};
      let stored: unknown = null;
      const video = {
        muted: false,
        playsInline: false,
        videoWidth: 320,
        videoHeight: 240,
        // Track set-order for srcObject. `play()` resolves a microtask later
        // so loadeddata fires synchronously inside the capture pipeline.
        get srcObject() {
          return stored;
        },
        set srcObject(v: unknown) {
          stored = v;
          srcObjectSetAt = ++stepCounter;
        },
        addEventListener: (type: string, fn: EventListener) => {
          readinessEvents.push(type);
          if (type === "loadeddata") {
            listenerAttachedAt = ++stepCounter;
          }
          (listeners[type] ??= []).push(fn);
        },
        removeEventListener: (type: string, fn: EventListener) => {
          listeners[type] = (listeners[type] ?? []).filter((l) => l !== fn);
        },
        play: vi.fn().mockImplementation(() => {
          // Fire loadeddata after one microtask, simulating the browser.
          queueMicrotask(() => {
            for (const fn of listeners["loadeddata"] ?? []) {
              fn(new Event("loadeddata"));
            }
          });
          return Promise.resolve();
        }),
      } as unknown as HTMLVideoElement;
      return video;
    }
    if (tag === "canvas") {
      const canvas = {
        width: 0,
        height: 0,
        getContext: () => ({ drawImage: vi.fn() }),
        toBlob: (cb: (b: Blob) => void) => {
          cb(new Blob([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], { type: "image/png" }));
        },
      } as unknown as HTMLCanvasElement;
      return canvas;
    }
    return realCreate(tag);
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("captureScreen", () => {
  it("returns a PNG Blob from the captured frame", async () => {
    const blob = await captureScreen();
    expect(blob).toBeInstanceOf(Blob);
    expect(blob.type).toBe("image/png");
  });

  it("registers the readiness listener BEFORE setting video.srcObject (no race)", async () => {
    await captureScreen();
    expect(listenerAttachedAt).toBeGreaterThan(0);
    expect(srcObjectSetAt).toBeGreaterThan(0);
    expect(listenerAttachedAt).toBeLessThan(srcObjectSetAt);
  });

  it("waits for `loadeddata`, not just `loadedmetadata` (frame must be decoded)", async () => {
    await captureScreen();
    expect(readinessEvents).toContain("loadeddata");
  });

  it("stops every MediaStream track after capture so the recording indicator clears", async () => {
    await captureScreen();
    expect(stoppedCount).toBeGreaterThan(0);
  });
});
