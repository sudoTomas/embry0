import { useEffect, useRef, useState } from "react";

/**
 * A short light travelling along a running card's top edge (index.css,
 * "motion: console board"): the job is alive. Its card must be `relative`.
 * The loop pauses while the card is off screen, so a long board never runs
 * lights nobody sees.
 */
export function RunningLight() {
  const ref = useRef<HTMLSpanElement>(null);
  const [onScreen, setOnScreen] = useState(true);

  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(([entry]) => setOnScreen(entry.isIntersecting));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return (
    <span
      ref={ref}
      aria-hidden="true"
      data-testid="running-light"
      data-paused={onScreen ? undefined : ""}
      className="console-light pointer-events-none absolute inset-x-0 top-0 h-px overflow-hidden"
    >
      <span className="console-light-run absolute inset-y-0 left-0 w-1/3" />
    </span>
  );
}
