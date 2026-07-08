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
    tier: null,
    provider_mode: null,
    model: null,
    attempts: 0,
    pr_url: null,
    error_message: null,
    validation_summary: null,
    total_cost_usd: 0,
  };
}

describe("columnForStatus", () => {
  it("maps every JobStatus to its lane", () => {
    for (const [status, lane] of Object.entries(EXPECTED_LANES) as [JobStatus, BoardColumnId][]) {
      expect(columnForStatus(status)).toBe(lane);
    }
  });

  it("throws on a status outside the union at runtime", () => {
    expect(() => columnForStatus("bogus" as JobStatus)).toThrow(/Unhandled job status/);
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

  it("uses lowercase labels", () => {
    for (const column of BOARD_COLUMNS) {
      expect(column.label).toBe(column.label.toLowerCase());
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
