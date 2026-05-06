/**
 * Phase 5D: page wrapper for the AffectedSetView.
 *
 * Routed under `/qa/runs/:runId/affected`. Pulls runId out of the URL
 * and delegates to AffectedSetView for everything else (loading, error,
 * 404, content). The wrapper exists so the route can render inside the
 * shared AppLayout chrome without leaking layout concerns into the view
 * component (which is also embeddable from QaRunDetailPage if we ever
 * decide to inline it).
 */
import { Link, useParams } from "react-router";
import { AffectedSetView } from "@/components/qa/AffectedSetView";

export function QaAffectedSetPage() {
  const { runId } = useParams<{ runId: string }>();
  if (!runId) {
    return <div className="p-6 text-white/50">No run id</div>;
  }
  return (
    <div>
      <div className="px-6 pt-4">
        <Link
          to={`/qa/runs/${encodeURIComponent(runId)}`}
          className="text-sm text-white/50 hover:underline"
        >
          ← run detail
        </Link>
      </div>
      <AffectedSetView runId={runId} />
    </div>
  );
}
