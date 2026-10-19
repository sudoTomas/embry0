import { useState, useCallback } from "react";
import { List, LayoutGrid, Plus } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { PageError } from "@/components/PageError";
import { TableSkeleton } from "@/components/TableSkeleton";
import { IssueListView } from "@/components/issues/IssueListView";
import { IssueBoardView } from "@/components/issues/IssueBoardView";
import { CreateIssueDialog } from "@/components/issues/CreateIssueDialog";
import { ProposalsSection } from "@/components/issues/ProposalsSection";
import { useIssueBoard, useIssues, useUpdateIssue } from "@/hooks/useIssues";
import { useIssuesStore } from "@/stores/issuesStore";
import { ISSUE_BOARD_COLUMNS } from "@/lib/constants";
import type { IssueStatus } from "@/lib/types";
import { toastApiError } from "@/api/errorToast";

const LIMIT = 20;
const BOARD_STATUSES = ISSUE_BOARD_COLUMNS.map((c) => c.status);

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

  // The board counts every issue per status, apart from the list's page.
  const board = useIssueBoard(
    BOARD_STATUSES,
    { priority: priorityFilter, repo: repoFilter, search },
    viewMode === "board",
  );

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
    updateIssue.mutate(
      { id: issueId, status: newStatus as IssueStatus },
      { onError: (e) => toastApiError(e, "Failed to move the issue") },
    );
  }, [updateIssue]);

  const repoOptions: string[] = data
    ? Array.from(new Set(data.issues.map((i) => i.repo).filter((r): r is string => r !== null)))
    : [];

  return (
    <Tabs
      defaultValue="list"
      value={viewMode}
      onValueChange={(v) => setViewMode(v as typeof viewMode)}
      className="space-y-6"
    >
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-bold">Issues</h1>
          {data && (
            <span className="inline-flex items-center rounded-full bg-white/[0.06] px-2.5 py-0.5 text-xs font-medium text-muted-foreground">
              {data.total}
            </span>
          )}
        </div>

        <div className="flex items-center gap-3">
          {/* View toggle: the shared tab bar, one selected style app-wide. */}
          <TabsList>
            <TabsTrigger value="list" className="gap-1.5">
              <List className="h-4 w-4" />
              List
            </TabsTrigger>
            <TabsTrigger value="board" className="gap-1.5">
              <LayoutGrid className="h-4 w-4" />
              Board
            </TabsTrigger>
          </TabsList>

          <Button onClick={() => setShowCreate(!showCreate)}>
            <Plus className="h-4 w-4 mr-1" />
            Create issue
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

      <ProposalsSection />

      {/* Content: the panel of the selected view tab. */}
      <TabsContent value={viewMode} className="mt-0">
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
          ) : board.isError ? (
            <PageError message="Failed to load the issue board" onRetry={board.refetch} />
          ) : board.isLoading ? (
            <TableSkeleton columns={6} rows={6} />
          ) : (
            <IssueBoardView issues={board.issues} totals={board.totals} onStatusChange={handleBoardStatusChange} />
          )
        ) : null}
      </TabsContent>
    </Tabs>
  );
}
