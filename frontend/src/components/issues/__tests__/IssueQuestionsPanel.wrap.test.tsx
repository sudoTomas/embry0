import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("@/hooks/useIssueInputs", () => ({
  useIssueInputs: () => ({
    data: [
      {
        id: "in-1",
        issue_id: "iss-1",
        job_id: "job-1",
        asking_node: "triage",
        question: "Is `a_very_long_identifier_with_no_break_points_at_all` the right target?",
        importance: "blocking",
        auto_answer: null,
        answer: null,
        answered_by: null,
        status: "pending",
        created_at: "2026-10-18T00:00:00Z",
        answered_at: null,
      },
    ],
  }),
  useAnswerIssueInput: () => ({ mutate: vi.fn(), isPending: false }),
}));

import { IssueQuestionsPanel } from "../IssueQuestionsPanel";

describe("IssueQuestionsPanel question text", () => {
  it("wraps inside its card and shows backtick spans as inline code", () => {
    render(<IssueQuestionsPanel issueId="iss-1" />);
    const text = screen.getByTestId("issue-question-text");
    expect(text.className).toContain("min-w-0");
    expect(text.className).toContain("[overflow-wrap:anywhere]");
    expect(text.querySelector("code")).toHaveTextContent("a_very_long_identifier_with_no_break_points_at_all");
    expect(text).not.toHaveTextContent("`");
  });
});
