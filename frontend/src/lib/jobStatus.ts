/**
 * The client's job status sets, once. Each mirrors a backend definition and
 * lib/__tests__/jobStatusParity.test.ts pins it to that source.
 *
 * TERMINAL_JOB_STATUSES: the statuses a job is finished in, FINISHED_STATUSES
 * in embry0/api/ws/streaming.py (the repository's TERMINAL_JOB_STATUSES plus
 * the post-delivery `pr_merged`/`pr_closed`; #62: `partial` was missing here).
 */
export const TERMINAL_JOB_STATUSES: ReadonlySet<string> = new Set([
  "completed", "failed", "cancelled", "expired", "partial", "pr_merged", "pr_closed", "skipped",
]);

/**
 * The statuses of a job that finished its work, FINISHED_WELL_STATUSES in
 * embry0/storage/schemas.py: `completed`, and the statuses merge sync moves it
 * to once its pull request merged or closed. Its report is still its own.
 */
export const FINISHED_WELL_JOB_STATUSES: ReadonlySet<string> = new Set(["completed", "pr_merged", "pr_closed"]);

/** The statuses POST /jobs/{id}/cancel accepts (cancel_job in embry0/api/v1/jobs.py). */
export const CANCELLABLE_JOB_STATUSES: ReadonlySet<string> = new Set([
  "pending", "running", "awaiting_input", "paused",
]);

export function isTerminalStatus(status?: string): boolean {
  return status !== undefined && TERMINAL_JOB_STATUSES.has(status);
}

export function isFinishedWell(status?: string): boolean {
  return status !== undefined && FINISHED_WELL_JOB_STATUSES.has(status);
}

export function isCancellable(status?: string): boolean {
  return status !== undefined && CANCELLABLE_JOB_STATUSES.has(status);
}

/** The terminal statuses a job reaches by succeeding: a node still running then finished with it. */
export function isSuccessfulTerminal(status?: string): boolean {
  return status === "completed" || status === "pr_merged" || status === "skipped";
}
