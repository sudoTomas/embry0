import { useState, useCallback, useMemo } from "react";
import { useJobs, useCancelJob } from "@/hooks/useJobs";
import { JobsTable } from "@/components/jobs/JobsTable";
import { CreateJobDialog } from "@/components/jobs/CreateJobDialog";
import { PageError } from "@/components/PageError";
import { TableSkeleton } from "@/components/TableSkeleton";
import { Button } from "@/components/ui/Button";
import { Plus } from "lucide-react";
import { toast } from "sonner";
import { ALL_JOB_STATUSES } from "@/lib/types/jobs";
import { toastApiError } from "@/api/errorToast";

export function JobsPage() {
  const [showCreate, setShowCreate] = useState(false);
  const [offset, setOffset] = useState(0);
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const [repoFilter, setRepoFilter] = useState<string | undefined>();
  const limit = 20;
  const jobFilters = useMemo(
    () => ({ limit, offset, status: statusFilter, repo: repoFilter }),
    [limit, offset, statusFilter, repoFilter],
  );
  const { data, isLoading, isError, refetch } = useJobs(jobFilters);
  const cancelJob = useCancelJob();

  const handleCancel = (jobId: string) => {
    cancelJob.mutate(jobId, {
      onSuccess: () => toast.success("Job cancelled"),
      onError: (e) => toastApiError(e, "Failed"),
    });
  };

  const handleStatusChange = useCallback((value: string | undefined) => {
    setStatusFilter(value);
    setOffset(0);
  }, []);

  const handleRepoChange = useCallback((value: string | undefined) => {
    setRepoFilter(value);
    setOffset(0);
  }, []);

  const repoOptions: string[] = data
    ? Array.from(new Set(data.jobs.flatMap((j) => (j.repo ? [j.repo] : []))))
    : [];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="title-pixel text-xl font-bold">Jobs</h1>
        <Button onClick={() => setShowCreate(!showCreate)}>
          <Plus className="h-4 w-4 mr-2" /> New job
        </Button>
      </div>

      {showCreate && <CreateJobDialog onClose={() => setShowCreate(false)} />}

      {isError ? (
        <PageError message="Failed to load jobs" onRetry={() => refetch()} />
      ) : isLoading ? (
        <TableSkeleton columns={7} rows={6} />
      ) : data ? (
        <JobsTable
          jobs={data.jobs}
          total={data.total}
          offset={offset}
          limit={limit}
          filters={{ status: statusFilter, repo: repoFilter }}
          repoOptions={repoOptions}
          statusOptions={[...ALL_JOB_STATUSES]}
          onStatusChange={handleStatusChange}
          onRepoChange={handleRepoChange}
          onPageChange={setOffset}
          onCancel={handleCancel}
          cancellingJobId={cancelJob.isPending ? cancelJob.variables : undefined}
        />
      ) : null}
    </div>
  );
}
