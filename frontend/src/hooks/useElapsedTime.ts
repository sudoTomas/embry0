import { useEffect, useState } from "react";

/** "3m 12s" between startedAt and finishedAt, ticking every second while the job runs. */
export function useElapsedTime(startedAt: string | null, finishedAt: string | null): string {
  const [elapsed, setElapsed] = useState("");
  useEffect(() => {
    if (!startedAt) return;
    const update = () => {
      const start = new Date(startedAt).getTime();
      const end = finishedAt ? new Date(finishedAt).getTime() : Date.now();
      const s = Math.floor((end - start) / 1000);
      const m = Math.floor(s / 60);
      const rs = s % 60;
      setElapsed(m > 0 ? `${m}m ${rs}s` : `${rs}s`);
    };
    update();
    if (!finishedAt) {
      const interval = setInterval(update, 1000);
      return () => clearInterval(interval);
    }
  }, [startedAt, finishedAt]);
  return elapsed;
}
