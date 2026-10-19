// Answering an agent's question with the row shape GET /jobs/{id}/inputs
// really returns: the issue_inputs row (id, asking_node, importance), not the
// input_id/category/options shape the cards once read. Each test drives the
// real card, hook and API module and checks the URL that would be POSTed.
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { api } from "@/api/client";
import { AwaitingInputCard } from "@/components/jobs/AwaitingInputCard";
import { AutoAnswerCard } from "@/components/jobs/AutoAnswerCard";
import { NeedsYouCard } from "@/components/console/NeedsYouCard";
import type { JobInput, JobResponse } from "@/lib/types";

function apiRow(id: string, question: string, issueId: string | null, overrides: Partial<JobInput> = {}): JobInput {
  return {
    id,
    issue_id: issueId,
    job_id: "job-abc",
    asking_node: "triage",
    question,
    importance: "blocking",
    auto_answer: null,
    answer: null,
    answered_by: null,
    status: "pending",
    created_at: "2026-10-18T00:00:00Z",
    answered_at: null,
    ...overrides,
  };
}

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>{ui}</QueryClientProvider>
    </MemoryRouter>,
  );
}

let posted: string[] = [];
beforeEach(() => {
  posted = [];
  vi.spyOn(api, "post").mockImplementation(async (url: string) => {
    posted.push(url);
    return { data: {} } as never;
  });
});

describe("answering an agent's question", () => {
  it("the Job page card posts each answer to its own input id", async () => {
    wrap(
      <AwaitingInputCard
        jobId="job-abc"
        jobInputs={[apiRow("inp-111", "Which DB?", "iss-1"), apiRow("inp-222", "Which port?", "iss-1")]}
      />,
    );
    const boxes = screen.getAllByPlaceholderText("Your answer...");
    fireEvent.change(boxes[0], { target: { value: "postgres" } });
    expect((boxes[1] as HTMLTextAreaElement).value, "typing in one question must not fill the other").toBe("");
    fireEvent.change(boxes[1], { target: { value: "5432" } });
    fireEvent.click(screen.getByRole("button", { name: /Submit 2 answers/ }));
    await waitFor(() => expect(posted).toHaveLength(2));
    expect(posted.sort()).toEqual([
      "/issues/iss-1/inputs/inp-111/answer",
      "/issues/iss-1/inputs/inp-222/answer",
    ]);
  });

  it("the Console card answers an issue-less job through the job route", async () => {
    const job = {
      job_id: "job-abc", status: "awaiting_input", repo: "o/r", task: "t", created_at: "2026-10-18T00:00:00Z",
      started_at: null, finished_at: null, issue_number: null, issue_id: null, pr_url: null,
      error_message: null, total_cost_usd: 0,
    } as JobResponse;
    wrap(<NeedsYouCard job={job} jobInputs={[apiRow("inp-333", "Which branch?", null)]} />);
    fireEvent.change(screen.getByPlaceholderText("Your answer..."), { target: { value: "main" } });
    fireEvent.click(screen.getByRole("button", { name: /Submit 1 answer/ }));
    await waitFor(() => expect(posted).toEqual(["/jobs/job-abc/inputs/inp-333/answer"]));
  });

  it("overriding an auto-answer on the logs page posts to the input id and shows the asking agent", async () => {
    wrap(
      <AutoAnswerCard
        jobId="job-abc"
        input={apiRow("inp-444", "Retry flaky test?", "iss-1", {
          status: "auto_answered",
          importance: "auto_answerable",
          auto_answer: "yes",
          asking_node: "review",
        })}
      />,
    );
    expect(screen.getByText("review")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Override" }));
    fireEvent.change(screen.getByPlaceholderText("Your answer..."), { target: { value: "no" } });
    fireEvent.click(screen.getByRole("button", { name: "Submit" }));
    await waitFor(() => expect(posted).toEqual(["/issues/iss-1/inputs/inp-444/answer"]));
  });
});
