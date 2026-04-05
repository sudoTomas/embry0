import { useState, useCallback } from "react";
import { List, LayoutGrid, Plus } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { PageError } from "@/components/PageError";
import { TableSkeleton } from "@/components/TableSkeleton";
import { IssueListView } from "@/components/issues/IssueListView";
import { IssueBoardView } from "@/components/issues/IssueBoardView";
import { CreateIssueDialog } from "@/components/issues/CreateIssueDialog";
import { useIssues, useUpdateIssue } from "@/hooks/useIssues";
import { useIssuesStore } from "@/stores/issuesStore";
import { cn } from "@/lib/utils";
import type { IssueStatus } from "@/lib/types";

const LIMIT = 20;

export function IssuesPage() {
  const { viewMode, setViewMode } = useIssuesStore();
  const [showCreate, setShowCreate] = useState(false);
  const updateIssue = useUpdateIssue();
  const [offset, setOffset] = useState(0);
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const [priorityFilter, setPriorityFilter] = useState<string | undefined>();
  const [repoFilter, setRepoFilter] = useState<string | undefined>();
  const [search, setSearch] = useState("");

  const { data, isLoading, isError, refetch } = useIssues({
    status: statusFilter,
    priority: priorityFilter,
    repo: repoFilter,
    search,
    limit: LIMIT,
    offset,
  });

  const handleStatusChange = useCallback((v: string | undefined) => {
    setStatusFilter(v);
    setOffset(0);
  }, []);

  const handlePriorityChange = useCallback((v: string | undefined) => {
    setPriorityFilter(v);
    setOffset(0);
  }, []);

  const handleRepoChange = useCallback((v: string | undefined) => {
    setRepoFilter(v);
    setOffset(0);
  }, []);

  const handleSearchChange = useCallback((v: string) => {
    setSearch(v);
    setOffset(0);
  }, []);

  const handleBoardStatusChange = useCallback((issueId: string, newStatus: string) => {
    updateIssue.mutate({ id: issueId, status: newStatus as IssueStatus });
  }, [updateIssue]);

  const repoOptions: string[] = data
    ? Array.from(new Set(data.issues.map((i) => i.repo).filter((r): r is string => r !== null)))
    : [];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold">Issues</h1>
          {data && (
            <span className="inline-flex items-center rounded-full bg-white/[0.06] px-2.5 py-0.5 text-xs font-medium text-muted-foreground">
              {data.total}
            </span>
          )}
        </div>

        <div className="flex items-center gap-3">
          {/* View toggle */}
          <div className="flex items-center rounded-lg border border-white/[0.1] overflow-hidden">
            <button
              onClick={() => setViewMode("list")}
              className={cn(
                "flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium transition-colors",
                viewMode === "list"
                  ? "bg-primary text-white"
                  : "text-muted-foreground hover:text-white"
              )}
              aria-label="List view"
            >
              <List className="h-4 w-4" />
              List
            </button>
            <button
              onClick={() => setViewMode("board")}
              className={cn(
                "flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium transition-colors",
                viewMode === "board"
                  ? "bg-primary text-white"
                  : "text-muted-foreground hover:text-white"
              )}
              aria-label="Board view"
            >
              <LayoutGrid className="h-4 w-4" />
              Board
            </button>
          </div>

          <Button onClick={() => setShowCreate(!showCreate)}>
            <Plus className="h-4 w-4 mr-1" />
            Create Issue
          </Button>
        </div>
      </div>

      {/* Create dialog */}
      {showCreate && (
        <CreateIssueDialog
          onClose={() => setShowCreate(false)}
          repos={repoOptions}
        />
      )}

      {/* Content */}
      {isError ? (
        <PageError message="Failed to load issues" onRetry={() => refetch()} />
      ) : isLoading ? (
        <TableSkeleton columns={8} rows={6} />
      ) : data ? (
        viewMode === "list" ? (
          <IssueListView
            issues={data.issues}
            total={data.total}
            offset={offset}
            limit={LIMIT}
            filters={{ status: statusFilter, priority: priorityFilter, repo: repoFilter }}
            repoOptions={repoOptions}
            onStatusChange={handleStatusChange}
            onPriorityChange={handlePriorityChange}
            onRepoChange={handleRepoChange}
            onSearchChange={handleSearchChange}
            searchValue={search}
            onPageChange={setOffset}
          />
        ) : (
          <IssueBoardView issues={data.issues} onStatusChange={handleBoardStatusChange} />
        )
      ) : null}
    </div>
  );
}
