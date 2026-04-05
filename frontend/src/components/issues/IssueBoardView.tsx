import { IssueCard } from "./IssueCard";
import { ISSUE_STATUS_COLORS, ISSUE_STATUS_ICONS } from "@/lib/constants";
import type { IssueResponse, IssueStatus } from "@/lib/types";

const BOARD_COLUMNS: { status: IssueStatus; label: string }[] = [
  { status: "open", label: "Open" },
  { status: "triaging", label: "Triaging" },
  { status: "in_progress", label: "In Progress" },
  { status: "closed", label: "Closed" },
];

interface IssueBoardViewProps {
  issues: IssueResponse[];
}

export function IssueBoardView({ issues }: IssueBoardViewProps) {
  const byStatus = Object.fromEntries(
    BOARD_COLUMNS.map(({ status }) => [
      status,
      issues.filter((i) => i.status === status),
    ]),
  ) as Record<IssueStatus, IssueResponse[]>;

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      {BOARD_COLUMNS.map(({ status, label }) => {
        const columnIssues = byStatus[status] ?? [];
        const icon = ISSUE_STATUS_ICONS[status] ?? "●";
        const color = ISSUE_STATUS_COLORS[status] ?? "text-muted-foreground";

        return (
          <div key={status} className="rounded-xl bg-card/50 border border-white/[0.05] flex flex-col min-h-[200px]">
            {/* Column header */}
            <div className="flex items-center gap-2 px-3 py-3 border-b border-white/[0.05]">
              <span className={`text-sm ${color}`}>{icon}</span>
              <span className="text-sm font-medium">{label}</span>
              <span className="ml-auto text-xs text-muted-foreground bg-white/[0.05] rounded-full px-2 py-0.5">
                {columnIssues.length}
              </span>
            </div>

            {/* Cards */}
            <div className="flex flex-col gap-2 p-2 flex-1">
              {columnIssues.length === 0 ? (
                <div className="flex flex-1 items-center justify-center py-8 text-xs text-muted-foreground">
                  No issues
                </div>
              ) : (
                columnIssues.map((issue) => (
                  <IssueCard key={issue.id} issue={issue} />
                ))
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
