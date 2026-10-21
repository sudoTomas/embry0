import { Link } from "react-router";
import { RunStatusBadge } from "./RunStatusBadge";
import type { RepoEntry } from "@/lib/types";

interface Props {
  repo: RepoEntry;
}

/** One repository's latest QA run, as a row of the QA table (`.data-table`). */
export function QaRepoCard({ repo }: Props) {
  return (
    <tr>
      <td className="w-full max-w-0 truncate">
        <Link
          to={`/qa/repos/${encodeURIComponent(repo.repo)}`}
          className="font-medium text-fg hover:text-primary hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          data-testid="qa-repo-card"
        >
          {repo.repo}
        </Link>
      </td>
      <td className="w-px">
        <RunStatusBadge status={repo.latest_status} />
      </td>
      <td className="num whitespace-nowrap text-fg-secondary">{repo.latest_app_count} {repo.latest_app_count === 1 ? "app" : "apps"}</td>
      <td className="hidden whitespace-nowrap font-mono text-xs text-fg-muted sm:table-cell">run {repo.latest_run_id.slice(0, 12)}…</td>
    </tr>
  );
}
