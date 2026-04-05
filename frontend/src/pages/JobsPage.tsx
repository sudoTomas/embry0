import { useState, useCallback } from "react";
import { useJobs, useRunJob, useCancelJob } from "@/hooks/useJobs";
import { JobsTable } from "@/components/jobs/JobsTable";
import { CreateJobDialog } from "@/components/jobs/CreateJobDialog";
import { PageError } from "@/components/PageError";
import { TableSkeleton } from "@/components/TableSkeleton";
import { Button } from "@/components/ui/Button";
import { Plus } from "lucide-react";
import { toast } from "sonner";
import type { JobStatus } from "@/lib/types";

export function JobsPage() {
  const [showCreate, setShowCreate] = useState(false);
  const [offset, setOffset] = useState(0);
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const [repoFilter, setRepoFilter] = useState<string | undefined>();
  const limit = 20;
  const { data, isLoading, isError, refetch } = useJobs({
    limit,
    offset,
    status: statusFilter,
    repo: repoFilter,
  });
  const runJob = useRunJob();
  const cancelJob = useCancelJob();

  const handleRun = (jobId: string) => {
    runJob.mutate(jobId, {
      onSuccess: () => toast.success("Job started"),
      onError: (e) => toast.error(`Failed: ${e.message}`),
    });
  };

  const handleCancel = (jobId: string) => {
    cancelJob.mutate(jobId, {
      onSuccess: () => toast.success("Job cancelled"),
      onError: (e) => toast.error(`Failed: ${e.message}`),
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

  const statusOptions: JobStatus[] = ["pending", "running", "completed", "failed", "cancelled"];
  const repoOptions: string[] = data
    ? Array.from(new Set(data.jobs.map((j) => j.repo).filter(Boolean)))
    : [];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Jobs</h1>
        <Button onClick={() => setShowCreate(!showCreate)}>
          <Plus className="h-4 w-4 mr-2" /> New Job
        </Button>
      </div>

      {showCreate && <CreateJobDialog onClose={() => setShowCreate(false)} />}

      {isError ? (
        <PageError message="Failed to load jobs" onRetry={() => refetch()} />
      ) : isLoading ? (
        <TableSkeleton columns={8} rows={6} />
      ) : data ? (
        <JobsTable
          jobs={data.jobs}
          total={data.total}
          offset={offset}
          limit={limit}
          filters={{ status: statusFilter, repo: repoFilter }}
          repoOptions={repoOptions}
          statusOptions={statusOptions}
          onStatusChange={handleStatusChange}
          onRepoChange={handleRepoChange}
          onPageChange={setOffset}
          onRun={handleRun}
          onCancel={handleCancel}
          runningJobId={runJob.isPending ? runJob.variables : undefined}
          cancellingJobId={cancelJob.isPending ? cancelJob.variables : undefined}
        />
      ) : null}
    </div>
  );
}
