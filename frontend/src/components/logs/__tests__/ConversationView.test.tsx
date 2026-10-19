import { describe, it, expect } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ConversationView } from "../ConversationView";
import { loadFixture } from "@/lib/__tests__/fixtures";

/** A gold fill, wash, text or frame. */
const GOLD = /(^|\s)(bg|text|border|fill|stroke)-primary(\/\d+)?(\s|$)/;

describe("ConversationView", () => {
  it("shows a tool call's summary in the header and its tool_input as the body", () => {
    render(<ConversationView events={loadFixture("issue-to-pr")} />);
    const header = screen.queryByText("Read README.md");
    expect(header, "the tool header lacks the summary").not.toBeNull();
    fireEvent.click(header!);
    expect(document.body.textContent, "the tool body lacks its tool_input").toContain('"file_path": "README.md"');
  });

  it("frames a tool call in the neutral border, not gold", () => {
    // Gold marks only the one thing on a view that needs a person; a
    // transcript holds many tool calls.
    render(<ConversationView events={loadFixture("issue-to-pr")} />);
    const block = screen.getByText("Read README.md").closest("button")?.parentElement;
    expect(block?.className).toContain("border-border");
    expect(block?.className).not.toMatch(/(^|\s)border-primary/);
  });

  it("names tools and the assistant in the neutral tones, never gold", () => {
    // A transcript repeats tool names and the Assistant label on every
    // turn; none of them is the one thing on the view that needs a person.
    const { container } = render(<ConversationView events={loadFixture("issue-to-pr")} />);
    expect(screen.getAllByText("Assistant").length, "no Assistant label rendered").toBeGreaterThan(0);
    const gold = [...container.querySelectorAll("*")].filter((el) => GOLD.test(el.getAttribute("class") ?? ""));
    expect(gold.map((el) => `${el.tagName.toLowerCase()} "${el.textContent?.trim() ?? ""}"`)).toEqual([]);
  });

  it("numbers turns within each run", () => {
    render(<ConversationView events={loadFixture("issue-to-pr")} />);
    expect(screen.queryAllByText("Turn 1"), "Turn 1 once per run").toHaveLength(4);
    expect(screen.queryAllByText("Turn 2"), "only the developer run has a Turn 2").toHaveLength(1);
  });

  it("separates runs with a divider naming node and agent", () => {
    render(<ConversationView events={loadFixture("issue-to-pr")} />);
    expect(screen.queryByText("Run 2 · developer · developer"), "no divider before the developer run").not.toBeNull();
    expect(screen.queryByText("Run 3 · developer · developer"), "no divider before the retried developer run").not.toBeNull();
    expect(screen.queryByText("Run 4 · review · reviewer"), "no divider before the review run").not.toBeNull();
  });

  it("pre-contract job: the tool call shows its summary", () => {
    render(<ConversationView events={loadFixture("legacy-rows")} />);
    expect(screen.queryByText("ls -la"), "the legacy tool call was dropped").not.toBeNull();
  });
});
