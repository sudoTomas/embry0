// The client's job status sets, pinned to the backend's own definitions so a
// status added there fails here instead of leaving a page that hides the
// outcome of a merged PR, a job nobody can cancel, or a poll that never stops.
import { describe, it, expect } from "vitest";
import schemasPy from "../../../../embry0/storage/schemas.py?raw";
import jobsRepoPy from "../../../../embry0/storage/repositories/jobs.py?raw";
import streamingPy from "../../../../embry0/api/ws/streaming.py?raw";
import jobsRoutePy from "../../../../embry0/api/v1/jobs.py?raw";
import { ALL_JOB_STATUSES } from "../types/jobs";
import { CANCELLABLE_JOB_STATUSES, TERMINAL_JOB_STATUSES } from "../jobStatus";

/** NAME -> value of `class JobStatus(StrEnum)` in embry0/storage/schemas.py. */
function backendEnum(): Map<string, string> {
  const body = /class JobStatus\(StrEnum\):\n([\s\S]*?)\n\n\nclass /.exec(schemasPy)?.[1];
  if (!body) throw new Error("class JobStatus not found in embry0/storage/schemas.py");
  return new Map([...body.matchAll(/^\s+([A-Z_]+) = "([a-z_]+)"/gm)].map((m) => [m[1], m[2]]));
}

function members(source: string, pattern: RegExp, what: string): string[] {
  const block = pattern.exec(source)?.[1];
  if (!block) throw new Error(`${what} not found`);
  const e = backendEnum();
  return [...block.matchAll(/JobStatus\.([A-Z_]+)/g)].map((m) => {
    const value = e.get(m[1]);
    if (!value) throw new Error(`${what} names JobStatus.${m[1]}, which the enum lacks`);
    return value;
  });
}

const sorted = (xs: Iterable<string>) => [...xs].sort();

describe("client job status sets match the backend", () => {
  it("lists exactly the backend's JobStatus values", () => {
    expect(sorted(ALL_JOB_STATUSES)).toEqual(sorted(backendEnum().values()));
  });

  it("finishes on the statuses the backend's event stream closes on", () => {
    // FINISHED_STATUSES (embry0/api/ws/streaming.py) = the repository's
    // TERMINAL_JOB_STATUSES plus the post-delivery statuses it adds.
    const terminal = members(jobsRepoPy, /_TERMINAL_STATUSES: frozenset\[str\] = frozenset\(\s*\{([\s\S]*?)\}\s*\)/, "jobs.py _TERMINAL_STATUSES");
    const added = members(streamingPy, /FINISHED_STATUSES: frozenset\[str\] = TERMINAL_JOB_STATUSES \| \{([^}]*)\}/, "streaming.py FINISHED_STATUSES");
    expect(sorted(TERMINAL_JOB_STATUSES)).toEqual(sorted(new Set([...terminal, ...added])));
  });

  it("offers Cancel on exactly the statuses POST /jobs/{id}/cancel accepts", () => {
    const tuple = /if job\["status"\] not in \(([^)]*)\):\n\s+raise HTTPException\(status_code=409, detail=f"Cannot cancel/.exec(jobsRoutePy)?.[1];
    if (!tuple) throw new Error("cancel_job's status check not found in embry0/api/v1/jobs.py");
    expect(sorted(CANCELLABLE_JOB_STATUSES)).toEqual(sorted([...tuple.matchAll(/"([a-z_]+)"/g)].map((m) => m[1])));
  });
});
