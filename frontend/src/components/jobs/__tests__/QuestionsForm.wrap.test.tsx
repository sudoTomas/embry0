import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QuestionsForm } from "../QuestionsForm";

// A question full of long `identifiers` must wrap inside a narrow board card
// rather than run over the next lane.
const QUESTION =
  "Should `LeadContactsChunkTimeoutHandlerWithAVeryLongName` move to " +
  "`services/very/long/path/that/never/breaks.py` first?";

describe("QuestionsForm question text", () => {
  it("renders backtick spans as inline code and lets the text break anywhere", () => {
    render(
      <QuestionsForm
        inputs={[{ id: "in-1", question: QUESTION, status: "pending" }]}
        onAnswer={vi.fn()}
      />,
    );
    const text = screen.getByTestId("question-text");
    expect(text.className).toContain("min-w-0");
    expect(text.className).toContain("[overflow-wrap:anywhere]");
    const code = text.querySelectorAll("code");
    expect([...code].map((c) => c.textContent)).toEqual([
      "LeadContactsChunkTimeoutHandlerWithAVeryLongName",
      "services/very/long/path/that/never/breaks.py",
    ]);
    expect(text).not.toHaveTextContent("`");
    expect(text).toHaveTextContent(/^Should LeadContacts.* first\?$/);
  });

  it("lets the question take the line below its number where the box is narrow", () => {
    render(<QuestionsForm inputs={[{ id: "in-1", question: QUESTION, asking_node: "triage", status: "pending" }]} onAnswer={vi.fn()} />);
    const text = screen.getByTestId("question-text");
    // Beside the number and the node, a board card left the question a
    // third of its width. The row wraps, and below 20rem the question takes
    // a line of its own.
    expect(text.parentElement).toHaveClass("flex-wrap");
    expect(text).toHaveClass("flex-1", "basis-80");
  });

  it("boxes each question unless the caller is the box already", () => {
    const question = [{ id: "in-1", question: "Which database?", status: "pending" as const }];
    const { unmount } = render(<QuestionsForm inputs={question} onAnswer={vi.fn()} />);
    const box = () => screen.getByTestId("question-text").parentElement!.parentElement!;
    expect(box()).toHaveClass("border", "p-3");
    unmount();
    render(<QuestionsForm inputs={question} onAnswer={vi.fn()} framed={false} />);
    expect(box().className).toBe("");
  });

  it("keeps a question with no backticks as plain text", () => {
    render(<QuestionsForm inputs={[{ id: "in-1", question: "Which database?", status: "pending" }]} onAnswer={vi.fn()} />);
    expect(screen.getByTestId("question-text").querySelector("code")).toBeNull();
    expect(screen.getByTestId("question-text")).toHaveTextContent("Which database?");
  });
});
