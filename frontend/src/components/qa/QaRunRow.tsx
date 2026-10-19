import { Link } from "react-router";
import { RunStatusBadge } from "./RunStatusBadge";
import type { RunListItem } from "@/lib/types";

interface Props {
  run: RunListItem;
}

/** One QA run as a row of the run-history table (`.data-table`). */
export function QaRunRow({ run }: Props) {
  return (
    <tr>
      <td className="w-full max-w-0 truncate">
        <Link
          to={`/qa/runs/${encodeURIComponent(run.job_id)}`}
          className="font-mono text-xs text-fg hover:text-primary hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          data-testid="qa-run-row"
        >
          {run.job_id}
        </Link>
      </td>
      <td className="w-px">
        <RunStatusBadge status={run.overall_status} />
      </td>
      <td className="num text-fg-secondary">{run.app_count}</td>
      <td className="num whitespace-nowrap text-fg-muted">{new Date(run.started_at).toLocaleString()}</td>
    </tr>
  );
}
