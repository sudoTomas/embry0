import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
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

function makeJobInput(overrides: Partial<JobInput> = {}): JobInput {
  return {
    input_id: "in-1",
    job_id: "abcd1234efgh5678",
    issue_id: "iss-1",
    question: "Which database should the migration target?",
    category: "technical",
    options: null,
    status: "pending",
    answer: null,
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

afterEach(() => {
  vi.useRealTimers();
});

describe("NeedsYouCard", () => {
  describe("TTL countdown", () => {
    it('renders "expires in 3h 12m" from paused_at + ttl_hours', () => {
      // Paused 48 minutes ago with a 4h TTL ⇒ 3h 12m remaining
      vi.useFakeTimers();
      vi.setSystemTime(new Date("2026-07-08T12:00:00Z"));

      renderCard({
        job: makeJob({ status: "paused" }),
        interrupt: { paused_at: "2026-07-08T11:12:00Z", ttl_hours: 4 },
      });

      expect(screen.getByTestId("ttl-countdown")).toHaveTextContent("expires in 3h 12m");
    });

    it("renders an expired marker once the TTL has elapsed", () => {
      vi.useFakeTimers();
      vi.setSystemTime(new Date("2026-07-08T12:00:00Z"));

      renderCard({
        job: makeJob({ status: "paused" }),
        interrupt: { paused_at: "2026-07-08T07:00:00Z", ttl_hours: 4 },
      });

      expect(screen.getByTestId("ttl-countdown")).toHaveTextContent("expired");
    });

    it("renders no countdown when the interrupt carries no TTL", () => {
      renderCard({
        job: makeJob({ status: "awaiting_input" }),
        interrupt: { reason: "Need input" },
      });

      expect(screen.queryByTestId("ttl-countdown")).toBeNull();
    });
  });

  describe("question text", () => {
    it("shows the first pending input's question", () => {
      renderCard({ job: makeJob({ status: "awaiting_input" }), jobInputs: [makeJobInput()] });
      expect(screen.getByTestId("needs-you-question")).toHaveTextContent(
        "Which database should the migration target?",
      );
    });

    it("falls back to the interrupt reason, then a generic line", () => {
      renderCard({
        job: makeJob({ status: "paused" }),
        interrupt: { reason: "Review retry cap reached" },
      });
      expect(screen.getByTestId("needs-you-question")).toHaveTextContent("Review retry cap reached");

      renderCard({ job: makeJob({ status: "paused" }) });
      expect(screen.getAllByTestId("needs-you-question")[1]).toHaveTextContent(
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
        expect(answerInput).toHaveBeenCalledWith("iss-1", "in-1", "postgres");
      });
    });

    it("renders no form when there are no renderable inputs", () => {
      renderCard({
        job: makeJob({ status: "paused" }),
        jobInputs: [makeJobInput({ status: "answered" })],
        interrupt: { reason: "Paused for review" },
      });

      expect(screen.queryByPlaceholderText("Your answer...")).toBeNull();
    });
  });

  it("still renders the amber card shell as a link to the job", () => {
    renderCard({ job: makeJob({ status: "awaiting_input" }) });
    expect(screen.getByRole("link", { name: "View job abcd1234efgh5678" })).toBeInTheDocument();
  });
});
