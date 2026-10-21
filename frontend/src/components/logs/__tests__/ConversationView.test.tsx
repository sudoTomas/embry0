import { describe, it, expect, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

vi.mock("@/hooks/useAgents", () => ({
  useAgents: () => ({ data: [{ type: "developer", display_name: "Developer", avatar: "smith", color: "amber", tagline: "" }], isLoading: false }),
}));

import { ConversationView } from "../ConversationView";
import { loadFixture } from "@/lib/__tests__/fixtures";

describe("ConversationView", () => {
  it("shows a tool call's summary in the header and its tool_input as the body", () => {
    render(<ConversationView events={loadFixture("issue-to-pr")} />);
    const header = screen.queryByText("Read README.md");
    expect(header, "the tool header lacks the summary").not.toBeNull();
    fireEvent.click(header!);
    expect(document.body.textContent, "the tool body lacks its tool_input").toContain('"file_path": "README.md"');
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

describe("run dividers", () => {
  it("show the agent's still sprite and display name", () => {
    render(<ConversationView events={loadFixture("issue-to-pr")} />);
    const dividers = screen.getAllByTestId("run-divider");
    expect(dividers.length, "one divider per run").toBeGreaterThan(1);
    const first = dividers[0];
    const sprite = first.querySelector("svg.sprite");
    expect(sprite, "divider carries the run's sprite").not.toBeNull();
    expect(sprite?.getAttribute("data-frames"), "a repeated sprite is still").toBe("1");
    expect(first.textContent).toContain("Run 1");
  });

  it("offer the full transcript only for a finished run with truncated events", () => {
    const events = loadFixture("issue-to-pr");
    const run = events.find((e) => e.type === "agent_completed")!.run_id!;
    const text = events.find((e) => e.type === "text" && e.run_id === run);
    const onFull = vi.fn();
    render(<ConversationView events={events} onFullTranscript={onFull} />);
    expect(screen.queryByRole("button", { name: /full transcript/i }), "no truncation, no offer").toBeNull();
    cleanup();
    const truncated = events.map((e) => (e === text ? { ...e, truncated: true, original_length: 9999 } : e));
    render(<ConversationView events={truncated} onFullTranscript={onFull} />);
    const button = screen.getByRole("button", { name: /full transcript/i });
    fireEvent.click(button);
    expect(onFull).toHaveBeenCalledWith({ runId: run, agent: text!.agent });
  });
});
