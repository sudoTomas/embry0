import { useCallback, useEffect, useRef } from "react";

/**
 * setTimeout for a component: every timer it starts is cleared when the
 * component unmounts, so its callback never runs, and never updates state,
 * once the component is gone. While the component is mounted it behaves
 * exactly like setTimeout.
 *
 * A browser shrugs off a state update on an unmounted component. A test
 * cannot: Vitest tears jsdom down after a file's last test, and a timer still
 * pending then fires with `window` gone, where React's update throws
 * "ReferenceError: window is not defined" outside any test (#256).
 */
export function useComponentTimeout(): (callback: () => void, ms: number) => void {
  const timers = useRef<Set<ReturnType<typeof setTimeout>>>(new Set());

  useEffect(() => {
    const pending = timers.current;
    return () => {
      for (const timer of pending) clearTimeout(timer);
      pending.clear();
    };
  }, []);

  return useCallback((callback: () => void, ms: number) => {
    const timer = setTimeout(() => {
      timers.current.delete(timer);
      callback();
    }, ms);
    timers.current.add(timer);
  }, []);
}
