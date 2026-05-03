import type { JSX } from "react";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/Tabs";
import { useQaAttempts } from "@/hooks/useQaResults";
import { QAAttemptCard } from "./QAAttemptCard";
import { QAScreenshotPanel } from "./QAScreenshotPanel";
import { QALiveLogTail } from "./QALiveLogTail";

interface Props {
  jobId: string;
  jobIsLive: boolean;
}

export function QATab({ jobId, jobIsLive }: Props): JSX.Element {
  const { data: attempts, isLoading } = useQaAttempts(jobId, jobIsLive);

  if (isLoading) {
    return <p className="text-muted-foreground">Loading QA attempts...</p>;
  }
  if (!attempts || attempts.length === 0) {
    return (
      <div className="text-center py-12 text-white/40">
        <p>No QA attempts yet.</p>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <div className="lg:col-span-2 space-y-4">
        {attempts.map((a) => (
          <QAAttemptCard key={a.attempt_n} jobId={jobId} attempt={a} />
        ))}
      </div>
      <div className="space-y-4">
        <div>
          <h3 className="text-sm text-white/60 mb-2">Live screenshot</h3>
          <QAScreenshotPanel jobId={jobId} jobIsLive={jobIsLive} />
        </div>
        {jobIsLive && (
          // Phase 3 starter: hard-coded gateway/frontend service tabs. Phase 4
          // (macro-lab integration) will read the user's compose service list
          // dynamically from qa.yaml.
          // The plan also specified an "All logs" tab passing service="" but the
          // backend's _SAFE_SERVICE regex (Task 3) rejects empty service names,
          // so it is omitted here. An "all-services" view requires backend
          // support out of scope for Phase 3.
          <Tabs defaultValue="gateway">
            <TabsList>
              <TabsTrigger value="gateway">gateway</TabsTrigger>
              <TabsTrigger value="frontend">frontend</TabsTrigger>
            </TabsList>
            <TabsContent value="gateway">
              <QALiveLogTail jobId={jobId} service="gateway" active={jobIsLive} />
            </TabsContent>
            <TabsContent value="frontend">
              <QALiveLogTail jobId={jobId} service="frontend" active={jobIsLive} />
            </TabsContent>
          </Tabs>
        )}
      </div>
    </div>
  );
}
