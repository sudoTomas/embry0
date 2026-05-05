import { Link } from "react-router";
import { RunStatusBadge } from "./RunStatusBadge";
import type { RunListItem } from "@/lib/types";

interface Props {
  run: RunListItem;
}

export function QaRunRow({ run }: Props) {
  return (
    <Link
      to={`/qa/runs/${encodeURIComponent(run.job_id)}`}
      className="grid grid-cols-[1fr,auto,auto] items-center gap-4 rounded-md border bg-card px-4 py-3 transition hover:opacity-90 focus:outline-none focus:ring-2 focus:ring-primary/40"
      data-testid="qa-run-row"
    >
      <span className="font-mono text-sm text-white/80 truncate">{run.job_id}</span>
      <RunStatusBadge status={run.overall_status} />
      <span className="text-xs text-white/40 whitespace-nowrap">
        {run.app_count} apps · {new Date(run.started_at).toLocaleString()}
      </span>
    </Link>
  );
}
