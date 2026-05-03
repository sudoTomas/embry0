import { useEffect, useState } from "react";

/**
 * Returns a cache-busted URL for the latest screenshot artifact.
 *
 * While `jobIsLive` is true, the URL's cache-buster ticks every 5s so consumers
 * (e.g. <img src=...>) refetch the latest frame. The interval is cleared on
 * unmount and restarted whenever `jobIsLive` toggles.
 *
 * Return type is `string | null` to preserve the spec contract: `null` is
 * reserved for future error handling. Today we always return a string and
 * delegate transport-level errors to the consumer's `<img onError>` handler
 * (see `QAScreenshotPanel`).
 */
export function useLatestScreenshot(
  jobId: string,
  jobIsLive: boolean,
): string | null {
  const [tick, setTick] = useState(Date.now());

  useEffect(() => {
    if (!jobIsLive) return;
    const interval = setInterval(() => setTick(Date.now()), 5000);
    return () => clearInterval(interval);
  }, [jobIsLive]);

  return `/api/v1/jobs/${jobId}/artifacts/screenshots/latest?_t=${tick}`;
}
