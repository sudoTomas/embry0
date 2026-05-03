import { useEffect, useState } from "react";
import type { JSX } from "react";
import { useLatestScreenshot } from "@/hooks/useLatestScreenshot";

interface Props {
  jobId: string;
  jobIsLive: boolean;
}

export function QAScreenshotPanel({ jobId, jobIsLive }: Props): JSX.Element {
  const url = useLatestScreenshot(jobId, jobIsLive);
  const [imgError, setImgError] = useState<boolean>(false);

  // Reset error state when the URL changes (new poll cycle) so a transient
  // 404 clears once the next poll succeeds.
  useEffect(() => {
    setImgError(false);
  }, [url]);

  const showImage = url && !imgError;

  return (
    <div className="border border-white/10 rounded overflow-hidden bg-black/40">
      {showImage ? (
        <img
          src={url}
          alt="Latest QA screenshot"
          className="w-full h-auto"
          onError={() => setImgError(true)}
        />
      ) : (
        <div className="p-8 text-center text-white/40 text-sm">
          No screenshot yet
        </div>
      )}
    </div>
  );
}
