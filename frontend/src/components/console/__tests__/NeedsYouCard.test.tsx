import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";

vi.mock("@/api/inputs", () => ({
  answerInput: vi.fn(),
}));

import { answerInput } from "@/api/inputs";
import { NeedsYouCard } from "../NeedsYouCard";
import { makeJob } from "./fixtures";
import type { JobInput } from "@/lib/types";

// The issue_inputs row GET /jobs/{id}/inputs returns (InputResponse).
function makeJobInput(overrides: Partial<JobInput> = {}): JobInput {
  return {
    id: "in-1",
    job_id: "abcd1234efgh5678",
    issue_id: "iss-1",
    asking_node: "triage",
    question: "Which database should the migration target?",
    importance: "blocking",
    status: "pending",
    answer: null,
    answered_by: null,
    auto_answer: null,
    created_at: "2026-07-08T11:00:00Z",
    answered_at: null,
    ...overrides,
  };
}

function renderCard(props: Parameters<typeof NeedsYouCard>[0]) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <NeedsYouCard {...props} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.mocked(answerInput).mockReset().mockResolvedValue(undefined);
});

describe("NeedsYouCard", () => {
  describe("question text", () => {
    it("shows the first pending input's question", () => {
      renderCard({ job: makeJob({ status: "awaiting_input" }), jobInputs: [makeJobInput()] });
      expect(screen.getByTestId("needs-you-question")).toHaveTextContent(
        "Which database should the migration target?",
      );
    });

    it("falls back to a generic line when no input is pending", () => {
      renderCard({ job: makeJob({ status: "paused" }) });
      expect(screen.getByTestId("needs-you-question")).toHaveTextContent(
        "Pipeline paused — open the job for details",
      );
    });
  });

  describe("inline answering", () => {
    it("renders the QuestionsForm inline for pending inputs", () => {
      renderCard({ job: makeJob({ status: "awaiting_input" }), jobInputs: [makeJobInput()] });

      expect(screen.getByPlaceholderText("Your answer...")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Submit 1 answer" })).toBeInTheDocument();
    });

    it("submits an answer through answerInput without navigating", async () => {
      renderCard({ job: makeJob({ status: "awaiting_input" }), jobInputs: [makeJobInput()] });

      fireEvent.change(screen.getByPlaceholderText("Your answer..."), {
        target: { value: "postgres" },
      });
      fireEvent.click(screen.getByRole("button", { name: "Submit 1 answer" }));

      await waitFor(() => {
        expect(answerInput).toHaveBeenCalledWith("iss-1", "in-1", "postgres", "abcd1234efgh5678");
      });
    });

    it("gives the question the card's width: no second box inside the card", () => {
      renderCard({ job: makeJob({ status: "awaiting_input" }), jobInputs: [makeJobInput()] });
      // The card is the amber box. A box of its own inside it took the
      // question another 26px of a lane a fifth of the board wide.
      const box = screen.getByTestId("question-text").parentElement!.parentElement!;
      expect(box).not.toHaveClass("border");
      expect(box).not.toHaveClass("p-3");
    });

    it("renders no form when there are no renderable inputs", () => {
      renderCard({
        job: makeJob({ status: "paused" }),
        jobInputs: [makeJobInput({ status: "answered" })],
      });

      expect(screen.queryByPlaceholderText("Your answer...")).toBeNull();
    });
  });

  it("still renders the amber card shell as a link to the job", () => {
    renderCard({ job: makeJob({ status: "awaiting_input" }) });
    expect(screen.getByRole("link", { name: "View job abcd1234efgh5678" })).toBeInTheDocument();
  });
});
