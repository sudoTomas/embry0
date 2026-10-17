/**
 * TERMINAL_JOB_STATUSES in embry0/storage/repositories/jobs.py plus the
 * post-delivery `pr_merged`/`pr_closed` (#62: `partial` was missing here).
 */
export const TERMINAL_JOB_STATUSES: ReadonlySet<string> = new Set([
  "completed", "failed", "cancelled", "expired", "partial", "pr_merged", "pr_closed", "skipped",
]);

export function isTerminalStatus(status?: string): boolean {
  return status !== undefined && TERMINAL_JOB_STATUSES.has(status);
}
