import type { JSX } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import type { QAAttemptListEntry } from "@/api/qa-artifacts";
import { useQaResult } from "@/hooks/useQaResults";
import { QAAcceptanceResults } from "./QAAcceptanceResults";

interface Props {
  jobId: string;
  attempt: QAAttemptListEntry;
}

export function QAAttemptCard({ jobId, attempt }: Props): JSX.Element {
  const { data: result, isLoading, isError } = useQaResult(
    jobId,
    attempt.attempt_n,
    attempt.has_result_json,
  );

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="text-base">
          Attempt {attempt.attempt_n}
          {result && (
            <span
              className={
                result.overall === "passed"
                  ? "ml-3 text-sm text-green-400"
                  : result.overall === "failed"
                    ? "ml-3 text-sm text-red-400"
                    : "ml-3 text-sm text-yellow-400"
              }
            >
              {result.overall}
            </span>
          )}
        </CardTitle>
        <div className="text-xs text-white/40">
          {attempt.screenshots_count} screenshots
        </div>
      </CardHeader>
      <CardContent>
        {!attempt.has_result_json ? (
          <p className="text-sm text-white/50 italic">
            No result.json yet -- attempt may still be running.
          </p>
        ) : isLoading ? (
          <p className="text-sm text-white/50">Loading result...</p>
        ) : isError ? (
          <p className="text-sm text-red-400">Failed to load result.</p>
        ) : result ? (
          <div className="space-y-3">
            <div className="text-xs text-white/50 grid grid-cols-3 gap-2">
              <span>Phase: {result.phase_reached}</span>
              <span>Boot: {Math.round(result.boot.duration_ms / 1000)}s</span>
              <span>Anomalies: {result.anomalies.length}</span>
            </div>
            <QAAcceptanceResults jobId={jobId} results={result.acceptance_results} />
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
