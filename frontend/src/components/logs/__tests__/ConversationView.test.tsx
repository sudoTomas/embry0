import { describe, it, expect, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

vi.mock("@/hooks/useAgents", () => ({
  useAgents: () => ({ data: [{ type: "developer", display_name: "Developer", avatar: "smith", color: "amber", tagline: "" }], isLoading: false }),
}));
vi.mock("@/lib/utils", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/utils")>();
  return { ...actual, highlightJson: vi.fn(actual.highlightJson) };
});
import { highlightJson } from "@/lib/utils";

import { ConversationView } from "../ConversationView";
import { loadFixture } from "@/lib/__tests__/fixtures";
import type { LogEvent } from "@/lib/types";

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

  it("a collapsed tool call does not re-highlight its input on a re-render", () => {
    const events = loadFixture("issue-to-pr");
    const { rerender } = render(<ConversationView events={events} />);
    const afterMount = vi.mocked(highlightJson).mock.calls.length;
    expect(afterMount, "the fixture carries tool calls").toBeGreaterThan(0);
    rerender(<ConversationView events={events} onFullTranscript={() => {}} />);
    expect(vi.mocked(highlightJson).mock.calls.length, "the page re-renders on every stream batch; a collapsed block must do no work").toBe(afterMount);
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

  it("offer the stored session once per agent, on its latest finished run", () => {
    // The stored session is one row per job and agent type, merged across the
    // agent's runs: the developer's two finished runs share it.
    const events = loadFixture("issue-to-pr");
    const developerRuns = events.filter((e) => e.type === "agent_completed" && e.agent === "developer").map((e) => e.run_id!);
    expect(developerRuns.length, "the fixture carries a retried developer").toBe(2);
    // The fixture's developer runs hold no text; give each one truncated line.
    const truncated = [
      ...events,
      ...developerRuns.map((run_id) => ({ type: "text", run_id, node: "developer", agent: "developer", text: "cut", truncated: true, original_length: 9999, timestamp: "2026-01-01T00:02:00Z" }) as LogEvent),
    ];
    const onFull = vi.fn();
    render(<ConversationView events={truncated} onFullTranscript={onFull} />);
    const buttons = screen.getAllByRole("button", { name: /full transcript/i });
    expect(buttons.length, "one offer per agent, on its latest finished run").toBe(1);
    fireEvent.click(buttons[0]);
    expect(onFull).toHaveBeenCalledWith({ runId: developerRuns[1], agent: "developer" });
  });
});
