/**
 * Phase 5E: page wrapper for the CacheAnalyticsView.
 *
 * Routed under `/qa/repos/:repo/cache`. Pulls `repo` out of the URL and
 * delegates to CacheAnalyticsView for everything else (loading, error,
 * empty-state). The page itself is a thin shell so the view component
 * stays embeddable from anywhere else in the dashboard.
 *
 * MVP: 30-day default window with no picker. A `<select>` for 7/30/90/180
 * days is a low-cost follow-up if the analytics page gets daily use.
 */
import { Link, useParams } from "react-router";
import { CacheAnalyticsView } from "@/components/qa/CacheAnalyticsView";

export function QaRepoCachePage() {
  const { repo } = useParams<{ repo: string }>();
  if (!repo) {
    return <div className="p-6 text-white/50">No repo</div>;
  }
  return (
    <div>
      <div className="px-6 pt-4">
        <Link
          to={`/qa/repos/${encodeURIComponent(repo)}`}
          className="text-sm text-white/50 hover:underline"
        >
          ← {repo} runs
        </Link>
      </div>
      <CacheAnalyticsView repo={repo} />
    </div>
  );
}
