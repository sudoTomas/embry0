import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { fetchJobs, fetchJob, createJob, runJob, cancelJob } from "@/api/jobs";
import type { JobCreateRequest, JobFilters } from "@/lib/types";

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
    refetchInterval: 10_000,
  });
}

export function useCreateJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (req: JobCreateRequest) => createJob(req),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useRunJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) => runJob(jobId),
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
