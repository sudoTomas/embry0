import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { fetchJobs, fetchJob, createJob, cancelJob, updateDoneWhen } from "@/api/jobs";
import type { JobCreateRequest, JobFilters } from "@/lib/types";
import type { DoneWhenEdit } from "@/lib/types/jobs";
import { isTerminalStatus } from "@/lib/jobStatus";

export function useJobs(filters: JobFilters = {}, refetchInterval = 10_000) {
  return useQuery({
    queryKey: ["jobs", filters],
    queryFn: () => fetchJobs(filters),
    refetchInterval,
  });
}

export function useJob(jobId: string | undefined) {
  return useQuery({
    queryKey: ["jobs", jobId],
    queryFn: () => fetchJob(jobId!),
    enabled: !!jobId,
    // A finished job changes no more; polling it stops.
    refetchInterval: (query) => (isTerminalStatus(query.state.data?.status) ? false : 10_000),
  });
}

export function useCreateJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (req: JobCreateRequest) => createJob(req),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useCancelJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) => cancelJob(jobId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

/** Replace a job's done-when list, then read the job again. */
export function useUpdateDoneWhen(jobId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: DoneWhenEdit) => updateDoneWhen(jobId!, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs", jobId] }),
  });
}
