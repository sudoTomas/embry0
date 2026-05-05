import { Link } from "react-router";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { RunStatusBadge } from "./RunStatusBadge";
import type { RepoEntry } from "@/lib/types";

interface Props {
  repo: RepoEntry;
}

export function QaRepoCard({ repo }: Props) {
  return (
    <Link
      to={`/qa/repos/${encodeURIComponent(repo.repo)}`}
      className="block transition hover:opacity-90 focus:outline-none focus:ring-2 focus:ring-primary/40 rounded-xl"
      data-testid="qa-repo-card"
    >
      <Card>
        <CardHeader className="flex-row items-center justify-between p-4 space-y-0">
          <CardTitle className="text-base font-semibold">{repo.repo}</CardTitle>
          <RunStatusBadge status={repo.latest_status} />
        </CardHeader>
        <CardContent className="p-4 pt-0 text-xs text-white/50">
          {repo.latest_app_count} apps · run {repo.latest_run_id.slice(0, 12)}…
        </CardContent>
      </Card>
    </Link>
  );
}
