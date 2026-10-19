import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockFetchJob = vi.fn();
vi.mock("@/api/jobs", () => ({ fetchJob: (...a: unknown[]) => mockFetchJob(...a) }));

import type { RepoStandardState } from "@/api/standards";
import { RepoStandardsRow } from "../RepoStandardsRow";
import { state } from "./fixtures";

function renderRow(standards: RepoStandardState[] | undefined, repo = "acme/widgets") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <RepoStandardsRow repo={repo} standards={standards} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockFetchJob.mockReset().mockResolvedValue({ job_id: "job-7", status: "running" });
});

describe("RepoStandardsRow", () => {
  it("shows the mode, score, findings and a link to the newest audit job with its status", async () => {
    renderRow([
      state("acme/widgets", {
        mode: "advise",
        score: 64,
        findings_open: 5,
        last_audit_at: "2026-01-01T00:00:00Z",
        last_audit_job_id: "job-7",
      }),
    ]);
    const row = screen.getByTestId("repo-standard-row");
    expect(within(row).getByTestId("repo-standard-link")).toHaveTextContent("Design standard");
    expect(within(row).getByTestId("repo-standard-link")).toHaveAttribute("href", "/orgs/acme/standards");
    expect(within(row).getByTestId("repo-standard-mode")).toHaveTextContent("Advise");
    expect(within(row).getByTestId("repo-standard-score")).toHaveTextContent("64");
    expect(within(row).getByTestId("repo-standard-findings")).toHaveTextContent("5");
    const job = within(row).getByTestId("repo-standard-job");
    expect(job).toHaveAttribute("href", "/jobs/job-7");
    expect(await within(job).findByText("Running")).toBeInTheDocument();
    expect(mockFetchJob).toHaveBeenCalledWith("job-7");
  });

  it("reads a repository never audited as off, with no score and no job", () => {
    renderRow([state("acme/widgets")]);
    expect(screen.getByTestId("repo-standard-mode")).toHaveTextContent("Off");
    expect(screen.getByTestId("repo-standard-score")).toHaveTextContent("none yet");
    expect(screen.getByTestId("repo-standard-findings")).toHaveTextContent("-");
    expect(screen.queryByTestId("repo-standard-job")).toBeNull();
    expect(mockFetchJob).not.toHaveBeenCalled();
  });

  it("links a nested GitLab repository to its top-level owner's standard", () => {
    renderRow([state("acme/group/app")], "Acme/group/app");
    expect(screen.getByTestId("repo-standard-link")).toHaveAttribute("href", "/orgs/acme/standards");
  });

  it("renders nothing for a server older than standards", () => {
    const { container } = renderRow(undefined);
    expect(container).toBeEmptyDOMElement();
  });
});
