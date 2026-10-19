// The Jobs list must tell jobs apart: a watcher tick's task is a long log
// briefing whose first words are the same for every tick, so the Task cell
// names the tick (stack and line count) instead, and the id shows the 8 hex
// characters after "job-", not 4.
import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { JobsTable } from "../JobsTable";
import type { JobResponse } from "@/lib/types";

const tick = (id: string, repo: string, lines: number): JobResponse =>
  ({
    job_id: id, status: "completed", repo, issue_number: null, issue_id: null, pr_url: null,
    total_cost_usd: 0.1, created_at: "2026-10-19T01:34:04Z", started_at: null, finished_at: null,
    error_message: null,
    task:
      `Analyze the following production log excerpts from the ${repo} stack (collected from Loki over ` +
      `the window 2026-10-19 00:32 .. 2026-10-19 01:34 UTC, ${lines} matching lines, newest first, each prefixed with i`,
    task_truncated: true,
  }) as unknown as JobResponse;

const issueJob = {
  ...tick("job-1e3ba760ad9f", "acme/app", 0),
  issue_id: "iss-1", issue_number: 665, task: "[watcher] Fix the contacts lookup failing closed", task_truncated: true,
} as JobResponse;

function renderTable(jobs: JobResponse[]) {
  return render(
    <MemoryRouter>
      <JobsTable
        jobs={jobs} total={jobs.length} offset={0} limit={20} filters={{}} repoOptions={[]} statusOptions={[]}
        onStatusChange={vi.fn()} onRepoChange={vi.fn()} onPageChange={vi.fn()} onCancel={vi.fn()}
      />
    </MemoryRouter>,
  );
}

describe("JobsTable rows", () => {
  it("names each watcher tick by its stack and line count", () => {
    renderTable([tick("job-fd155299c8a4", "acme/backend", 22), tick("job-ce21f7c6a8d0", "acme/ml", 1)]);
    const [first, second] = screen.getAllByRole("link", { name: /View job/ });
    expect(within(first).getByText("Watcher tick, backend, 22 lines")).toBeInTheDocument();
    expect(within(second).getByText("Watcher tick, ml, 1 line")).toBeInTheDocument();
  });

  it("shows an issue's job by the issue's title", () => {
    renderTable([issueJob]);
    expect(screen.getByText("[watcher] Fix the contacts lookup failing closed")).toBeInTheDocument();
  });

  it("shows 8 characters of the id, after the job- prefix", () => {
    renderTable([tick("job-fd155299c8a4", "acme/backend", 22)]);
    const id = screen.getByText("fd155299");
    expect(id).toHaveAttribute("title", "job-fd155299c8a4");
  });

  it("offers Cancel on every status the API cancels, and logs on every job that ran", () => {
    const waiting = { ...tick("job-aaaa1111bbbb", "acme/app", 3), status: "awaiting_input" } as JobResponse;
    const merged = { ...tick("job-cccc2222dddd", "acme/app", 3), status: "pr_merged" } as JobResponse;
    const partial = { ...tick("job-eeee3333ffff", "acme/app", 3), status: "partial" } as JobResponse;
    renderTable([waiting, merged, partial]);
    const [w, m, p] = screen.getAllByRole("link", { name: /View job/ });
    expect(within(w).getByTitle("Cancel"), "an awaiting_input job can be cancelled").toBeInTheDocument();
    expect(within(m).queryByTitle("Cancel")).toBeNull();
    expect(within(m).getByTitle("View Logs"), "a merged job keeps its logs").toBeInTheDocument();
    expect(within(p).getByTitle("View Logs"), "a partial job keeps its logs").toBeInTheDocument();
  });
});
