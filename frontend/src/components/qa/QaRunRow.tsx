import { Link } from "react-router";
import { RunStatusBadge } from "./RunStatusBadge";
import { formatDateTime } from "@/lib/utils";
import type { RunListItem } from "@/lib/types";

interface Props {
  run: RunListItem;
}

/** One QA run as a row of the run-history table (`.data-table`). The run id is
 * the row's only link, so it never truncates; below sm the app count steps
 * aside so the id, the status and the start time fit a phone. */
export function QaRunRow({ run }: Props) {
  return (
    <tr>
      <td className="w-full whitespace-nowrap">
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
      <td className="num hidden text-fg-secondary sm:table-cell">{run.app_count}</td>
      <td className="num whitespace-nowrap text-fg-muted">{formatDateTime(run.started_at)}</td>
    </tr>
  );
}
