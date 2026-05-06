/**
 * Phase 5F: page wrapper for the FlakeHeatmap.
 *
 * Routed under `/qa/repos/:repo/flake`. Pulls `repo` out of the URL and
 * delegates to FlakeHeatmap for everything else (loading, error, empty-
 * state). The page itself is a thin shell so the heatmap component stays
 * embeddable elsewhere in the dashboard.
 *
 * MVP: 7-day default with no picker. A `<select>` for 7/30/90 days is a
 * low-cost follow-up if the heatmap proves useful day-to-day.
 */
import { Link, useParams } from "react-router";
import { FlakeHeatmap } from "@/components/qa/FlakeHeatmap";

export function QaRepoFlakePage() {
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
      <FlakeHeatmap repo={repo} />
    </div>
  );
}
