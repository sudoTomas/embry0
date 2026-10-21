import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchJobs } from "@/api/jobs";
import {
  fetchOwnerStandards,
  fetchStandardVersions,
  setRepoStandardMode,
  startStandardAudit,
  type StandardMode,
} from "@/api/standards";

/** While an audit runs, the unfinished jobs are read this often. */
export const AUDIT_POLL_MS = 10_000;

/** A job's statuses before it finishes, in the order it usually moves through
 * them. The server refuses a second audit while the newest is in any of them. */
export const UNFINISHED_JOB_STATUSES = ["pending", "running", "awaiting_input", "paused"] as const;

/** The jobs list's largest page. */
const UNFINISHED_LIST_LIMIT = 200;

/** Under "jobs", so creating or cancelling a job reads it again too. */
export const UNFINISHED_JOBS_KEY = ["jobs", { unfinished: true }] as const;

/**
 * The status of every unfinished job, by id, from the jobs list in brief:
 * one small request per unfinished status, however many repositories are
 * audited, where a job's own read carries the whole standard in its task.
 * The statuses are read one after another in the order a job moves, so a
 * job that moves on between two reads is seen twice rather than missed. A
 * job past the page's 200 is missed; the server's 409 still refuses it.
 */
export async function fetchUnfinishedJobs(): Promise<Record<string, string>> {
  const byId: Record<string, string> = {};
  for (const status of UNFINISHED_JOB_STATUSES) {
    const { jobs } = await fetchJobs({ status, limit: UNFINISHED_LIST_LIMIT });
    for (const job of jobs) byId[job.job_id] = job.status;
  }
  return byId;
}

export function useOwnerStandards(owner: string, enabled = true) {
  return useQuery({
    queryKey: ["standards", owner],
    queryFn: () => fetchOwnerStandards(owner),
    enabled: !!owner && enabled,
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
 * The status of each audit job that is unfinished, by id; a finished or
 * unknown job is not in the map. The unfinished jobs are read from the jobs
 * list, read afresh on each visit (an audit may have started elsewhere since)
 * and polled only while one of these audits is in it. When an audit
 * this view saw unfinished leaves it, the owner's states and the repository
 * details are read again: that is when an audit's score and findings land.
 */
export function useAuditJobStatuses(owner: string, jobIds: readonly string[]): Map<string, string> {
  const qc = useQueryClient();
  const ids = new Set(jobIds);
  const unfinished = useQuery({
    queryKey: UNFINISHED_JOBS_KEY,
    queryFn: fetchUnfinishedJobs,
    enabled: jobIds.length > 0,
    staleTime: 0,
    retry: false,
    refetchInterval: (query) => (Object.keys(query.state.data ?? {}).some((id) => ids.has(id)) ? AUDIT_POLL_MS : false),
  });
  const statuses = new Map<string, string>();
  for (const id of jobIds) {
    const status = unfinished.data?.[id];
    if (status) statuses.set(id, status);
  }

  const busyKey = [...statuses.keys()].join(",");
  const seenBusy = useRef<Set<string>>(new Set());
  useEffect(() => {
    const busy = new Set(busyKey.split(",").filter(Boolean));
    const done = [...seenBusy.current].filter((id) => !busy.has(id));
    seenBusy.current = busy;
    if (done.length > 0) {
      void qc.invalidateQueries({ queryKey: ["standards", owner] });
      void qc.invalidateQueries({ queryKey: ["repos", "detail"] });
    }
  }, [busyKey, owner, qc]);

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

/** Start an audit (operator); the new job id lands on the state and the job
 * is unfinished, so the states and the unfinished jobs are read again. */
export function useStartAudit(owner: string, kind: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (repo: string) => startStandardAudit(owner, kind, repo),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ["standards", owner] });
      void qc.invalidateQueries({ queryKey: ["repos", "detail"] });
      void qc.invalidateQueries({ queryKey: UNFINISHED_JOBS_KEY });
    },
  });
}
