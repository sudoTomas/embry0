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
  /** Lowercase header label. */
  label: string;
  tint: BoardColumnTint;
}

/** Column order and header treatment, left to right. */
export const BOARD_COLUMNS: readonly BoardColumnConfig[] = [
  { id: "needs_you", label: "needs you", tint: "amber" },
  { id: "queued", label: "queued", tint: "neutral" },
  { id: "running", label: "running", tint: "neutral" },
  { id: "done", label: "done", tint: "success" },
  { id: "failed", label: "failed", tint: "neutral" },
];

/**
 * Map a job status to its board lane. Exhaustive over JobStatus — adding a
 * new status without assigning it a lane is a compile-time error, not a
 * silently invisible card.
 *
 * Note: embry0 has no "queued" status; `pending` is it (the `queued` name
 * belongs to the legacy companion agent backend).
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
    case "completed":
    case "pr_merged":
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
      throw new Error(`Unhandled job status: ${String(unhandled)}`);
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
