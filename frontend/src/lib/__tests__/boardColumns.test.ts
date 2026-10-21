import { describe, expect, it } from "vitest";
import { BOARD_COLUMNS, columnForStatus, groupJobsByColumn } from "../boardColumns";
import type { BoardColumnId } from "../boardColumns";
import type { JobResponse, JobStatus } from "@/lib/types";

// Typed as Record<JobStatus, …> so a new status added to the union without a
// row here is a compile error — the runtime loop then proves the mapping.
const EXPECTED_LANES: Record<JobStatus, BoardColumnId> = {
  awaiting_input: "needs_you",
  paused: "needs_you",
  pending: "queued",
  running: "running",
  completed: "done",
  pr_merged: "done",
  skipped: "done",
  failed: "failed",
  partial: "failed",
  cancelled: "failed",
  expired: "failed",
  pr_closed: "failed",
};

function makeJob(status: JobStatus): JobResponse {
  return {
    job_id: `job-${status}`,
    status,
    repo: "acme/widgets",
    task: "Do the thing",
    created_at: "2026-07-08T10:00:00Z",
    started_at: null,
    finished_at: null,
    issue_number: null,
    pr_url: null,
    error_message: null,
    total_cost_usd: 0,
  };
}

describe("columnForStatus", () => {
  it("maps every JobStatus to its lane", () => {
    for (const [status, lane] of Object.entries(EXPECTED_LANES) as [JobStatus, BoardColumnId][]) {
      expect(columnForStatus(status)).toBe(lane);
    }
  });

  it("puts a status this build does not know in the Failed lane instead of throwing", () => {
    // A deploy can add a status while an open tab holds the older bundle; the
    // board must still render that job (its pill names the status).
    expect(columnForStatus("reverted" as JobStatus)).toBe("failed");
    const grouped = groupJobsByColumn([makeJob("reverted" as JobStatus)]);
    expect(grouped.failed.map((j) => j.job_id)).toEqual(["job-reverted"]);
  });
});

describe("BOARD_COLUMNS", () => {
  it("orders the five lanes left to right with Needs You first", () => {
    expect(BOARD_COLUMNS.map((c) => c.id)).toEqual([
      "needs_you",
      "queued",
      "running",
      "done",
      "failed",
    ]);
  });

  it("tints the actionable and done lanes (Speculum lane-tint idiom)", () => {
    const byId = Object.fromEntries(BOARD_COLUMNS.map((c) => [c.id, c]));
    expect(byId.needs_you.tint).toBe("amber");
    expect(byId.done.tint).toBe("success");
    expect(byId.running.tint).toBe("neutral");
  });

  it("uses sentence-case labels", () => {
    for (const column of BOARD_COLUMNS) {
      const rest = column.label.slice(1);
      expect(column.label.charAt(0)).toBe(column.label.charAt(0).toUpperCase());
      expect(rest).toBe(rest.toLowerCase());
    }
  });
});

describe("groupJobsByColumn", () => {
  it("buckets a mixed job list into the five lanes", () => {
    const statuses: JobStatus[] = ["running", "pending", "awaiting_input", "completed", "failed", "pr_merged"];
    const grouped = groupJobsByColumn(statuses.map(makeJob));

    expect(grouped.running.map((j) => j.status)).toEqual(["running"]);
    expect(grouped.queued.map((j) => j.status)).toEqual(["pending"]);
    expect(grouped.needs_you.map((j) => j.status)).toEqual(["awaiting_input"]);
    expect(grouped.done.map((j) => j.status)).toEqual(["completed", "pr_merged"]);
    expect(grouped.failed.map((j) => j.status)).toEqual(["failed"]);
  });

  it("always includes every lane key, even for an empty list", () => {
    const grouped = groupJobsByColumn([]);
    expect(Object.keys(grouped).sort()).toEqual(["done", "failed", "needs_you", "queued", "running"]);
    expect(grouped.needs_you).toEqual([]);
  });
});
