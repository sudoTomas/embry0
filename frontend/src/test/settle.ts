import { act } from "@testing-library/react";

/**
 * Wait `ms` inside `act`, before a check that something did NOT happen (#732).
 *
 * A plain `await new Promise((r) => setTimeout(r, ms))` passes such a check whatever the code does on a loaded host:
 * the event loop stalls past the code's own timer and the test's wait together, both run back to back, and the test
 * reads before React has rendered what the code did, so the absence it checks is always there. Inside `act`, React
 * has rendered everything the code scheduled before the wait resolves, however late the timers ran (#731). The wait
 * gives the timer queue a few turns more, for a timer the code scheduled only once the loop was free, and when a
 * stall overran it, it waits `ms` again, so what came after the stall has as long as the check meant to give it.
 *
 * `EMBRY0_TEST_STALL_MS` busies the event loop that long once the wait is set, as a loaded host does, so a run can
 * show that a check holds up under load and still fails when the code does what it must not:
 * `EMBRY0_TEST_STALL_MS=300 npx vitest run <files>`.
 */
export async function settle(ms: number, stalledFor: number = stallMs()): Promise<void> {
  const waited = act(async () => {
    const from = Date.now();
    await later(ms);
    const overran = Date.now() - from > ms + OVERRUN_MS;
    // Then a few turns of the timer queue. What a stalled loop let through only once it was free, react-query's
    // notice of an answer among it, is a timer of 0 ms scheduled after the wait's own timer had already expired:
    // without these turns the wait resolves first, and React has nothing to render yet when the check reads.
    await turns();
    // A wait the loop overran (a stall) gave what came after the stall no time of its own, only those turns: a
    // longer chain of timers, or one step of a few ms, would still be out. So it waits `ms` again, now that the
    // loop is free.
    if (overran) {
      await later(ms);
      await turns();
    }
  });
  stall(stalledFor);
  await waited;
}

/** The timer-queue turns {@link settle} gives what a stalled loop scheduled late. */
const TURNS = 3;
/** How far past `ms` a wait may run before {@link settle} reads it as overrun by a stall. */
const OVERRUN_MS = 25;

async function turns(): Promise<void> {
  for (let turn = 0; turn < TURNS; turn += 1) await later(0);
}

function later(ms: number): Promise<void> {
  return new Promise<void>((resolve) => setTimeout(resolve, ms));
}

/** Busy the event loop for `ms`: what a loaded host does to it. */
export function stall(ms: number): void {
  const from = Date.now();
  while (Date.now() - from < ms) {
    // busy
  }
}

/** The stall a run asks for with `EMBRY0_TEST_STALL_MS`; 0 without it. */
export function stallMs(): number {
  const env = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process?.env;
  const ms = Number(env?.EMBRY0_TEST_STALL_MS ?? 0);
  return Number.isFinite(ms) && ms > 0 ? ms : 0;
}
