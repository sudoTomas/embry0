import { onTestFinished } from "vitest";
import { FIRST_PAINT_WAIT_MS, firstPaintWait } from "@/hooks/useFirstPaint";

/** Out of reach of any stall a test asks for: an hour. */
const OUT_OF_REACH_MS = 60 * 60 * 1000;

/**
 * For the test that calls it, a page's first paint waits for its answers however long the event loop stalls: its
 * fallback (FIRST_PAINT_WAIT_MS, 1.5 s) is put out of reach until the test ends. A test that checks a page did not
 * paint before an answer would otherwise fail, with nothing wrong, once its stalls add up past the fallback
 * (`EMBRY0_TEST_STALL_MS`, src/test/settle.ts).
 */
export function holdFirstPaint(): void {
  firstPaintWait.ms = OUT_OF_REACH_MS;
  onTestFinished(() => {
    firstPaintWait.ms = FIRST_PAINT_WAIT_MS;
  });
}
