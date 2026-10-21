import type { JobResponse, JobStatus } from "@/lib/types";

/** The five board lanes, left to right. Lanes are derived from JobStatus and
 * never hand-set — statuses are machine-driven, so the board is read-only. */
export type BoardColumnId = "needs_you" | "queued" | "running" | "done" | "failed";

/** Lane tint (the Speculum lane-tint idiom in embry0's own tokens): the
 * actionable Needs You lane is amber, Done is the success color, the rest
 * stay neutral. */
export type BoardColumnTint = "amber" | "success" | "neutral";

export interface BoardColumnConfig {
  id: BoardColumnId;
  /** Sentence-case header label. */
  label: string;
  tint: BoardColumnTint;
}

/** Column order and header treatment, left to right. */
export const BOARD_COLUMNS: readonly BoardColumnConfig[] = [
  { id: "needs_you", label: "Needs you", tint: "amber" },
  { id: "queued", label: "Queued", tint: "neutral" },
  { id: "running", label: "Running", tint: "neutral" },
  { id: "done", label: "Done", tint: "success" },
  { id: "failed", label: "Failed", tint: "neutral" },
];

/**
 * Map a job status to its board lane. Exhaustive over JobStatus — adding a
 * new status without assigning it a lane is a compile-time error, not a
 * silently invisible card; an unknown status at runtime lands in Failed.
 *
 * Note: embry0 has no "queued" status; `pending` is it — the lane keeps
 * the operator-facing "queued" name (and /stats reports the same alias).
 */
export function columnForStatus(status: JobStatus): BoardColumnId {
  switch (status) {
    case "awaiting_input":
    case "paused":
      return "needs_you";
    case "pending":
      return "queued";
    case "running":
      return "running";
    // skipped (#474): ended without work because an open PR already covers
    // the ticket — nothing failed and nothing is waiting on the operator.
    case "completed":
    case "pr_merged":
    case "skipped":
      return "done";
    case "failed":
    case "partial":
    case "cancelled":
    case "expired":
    case "pr_closed":
      return "failed";
    default: {
      // Compile-time exhaustiveness guard: a new JobStatus lands here as a
      // type error until it is assigned a lane above.
      const unhandled: never = status;
      // At runtime a status this bundle does not know (a deploy added it
      // while a tab held the older bundle; the API types status as a plain
      // string) must not take the board down. Failed is the lane for what
      // is neither a known success, a wait nor a run, and the card's own
      // pill names the status.
      void unhandled;
      return "failed";
    }
  }
}

/** Bucket a job list into the five lanes. Every lane key is always present
 * (empty array, not undefined) so the board renders all columns — including
 * an empty Needs You — without per-column existence checks. */
export function groupJobsByColumn(jobs: JobResponse[]): Record<BoardColumnId, JobResponse[]> {
  const grouped: Record<BoardColumnId, JobResponse[]> = {
    needs_you: [],
    queued: [],
    running: [],
    done: [],
    failed: [],
  };
  for (const job of jobs) {
    grouped[columnForStatus(job.status)].push(job);
  }
  return grouped;
}
