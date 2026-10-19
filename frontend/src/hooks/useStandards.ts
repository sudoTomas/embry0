import { useEffect, useRef } from "react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchJob } from "@/api/jobs";
import {
  fetchOwnerStandards,
  fetchStandardVersions,
  setRepoStandardMode,
  startStandardAudit,
  type StandardMode,
} from "@/api/standards";
import { isTerminalStatus } from "@/lib/jobStatus";

/** While an audit runs, its job is read this often. */
export const AUDIT_POLL_MS = 10_000;

export function useOwnerStandards(owner: string) {
  return useQuery({
    queryKey: ["standards", owner],
    queryFn: () => fetchOwnerStandards(owner),
    enabled: !!owner,
  });
}

export function useStandardVersions(owner: string, kind: string, enabled = true) {
  return useQuery({
    queryKey: ["standards", owner, kind, "versions"],
    queryFn: () => fetchStandardVersions(owner, kind),
    enabled: !!owner && enabled,
  });
}

/**
 * The status of each audit job, by id. Each job is read with the job page's
 * own query key, so both share one cache entry, and polls until it
 * finishes. When a job this view saw unfinished finishes, the owner's
 * states and the repository details are read again: that is when an audit's
 * score and findings land. A job that cannot be read has no status.
 */
export function useAuditJobStatuses(owner: string, jobIds: readonly string[]): Map<string, string> {
  const qc = useQueryClient();
  const results = useQueries({
    queries: jobIds.map((id) => ({
      queryKey: ["jobs", id],
      queryFn: () => fetchJob(id),
      retry: false,
      refetchInterval: (query: { state: { data?: { status?: string } } }) =>
        isTerminalStatus(query.state.data?.status) ? false : AUDIT_POLL_MS,
    })),
  });
  const statuses = new Map<string, string>();
  results.forEach((result, i) => {
    const status = result.data?.status;
    if (typeof status === "string") statuses.set(jobIds[i], status);
  });

  const busy = jobIds.filter((id) => statuses.has(id) && !isTerminalStatus(statuses.get(id)));
  const finished = jobIds.filter((id) => isTerminalStatus(statuses.get(id)));
  const busyKey = busy.join(",");
  const finishedKey = finished.join(",");
  const seenBusy = useRef<Set<string>>(new Set());
  useEffect(() => {
    const done = finishedKey.split(",").filter((id) => id && seenBusy.current.has(id));
    seenBusy.current = new Set(busyKey.split(",").filter(Boolean));
    if (done.length > 0) {
      void qc.invalidateQueries({ queryKey: ["standards", owner] });
      void qc.invalidateQueries({ queryKey: ["repos", "detail"] });
    }
  }, [busyKey, finishedKey, owner, qc]);

  return statuses;
}

/** Set one repository's mode (admin); the owner's states and the repository details are read again. */
export function useSetRepoMode(owner: string, kind: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ repo, mode }: { repo: string; mode: StandardMode }) => setRepoStandardMode(owner, kind, repo, mode),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ["standards", owner] });
      void qc.invalidateQueries({ queryKey: ["repos", "detail"] });
    },
  });
}

/** Start an audit (operator); the new job id lands on the state, so the states are read again. */
export function useStartAudit(owner: string, kind: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (repo: string) => startStandardAudit(owner, kind, repo),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ["standards", owner] });
      void qc.invalidateQueries({ queryKey: ["repos", "detail"] });
    },
  });
}
