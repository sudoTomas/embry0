import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { VirtuosoMockContext } from "react-virtuoso";
import type { ReactElement } from "react";

vi.mock("@/hooks/useAgents", () => ({
  useAgents: () => ({ data: [{ type: "developer", display_name: "Developer", avatar: "smith", color: "amber", tagline: "" }], isLoading: false }),
}));

import { StructuredView } from "../StructuredView";
import { loadFixture } from "@/lib/__tests__/fixtures";
import type { LogEvent } from "@/lib/types";

// Without a mock viewport react-virtuoso renders no items in jsdom (RawView.test shows it).
function renderVirtual(ui: ReactElement, itemCount = 200) {
  return render(
    <VirtuosoMockContext.Provider value={{ viewportHeight: 40 * itemCount, itemHeight: 40 }}>{ui}</VirtuosoMockContext.Provider>,
  );
}

// The first card for a tool; the fixture's retried developer run calls Bash a second time.
const toolButton = (name: string) =>
  screen.queryAllByRole("button", { name: new RegExp(`^${name}(?![a-z])`) })[0] ?? null;

describe("StructuredView", () => {
  it("renders a card per top-level tool call; subagent calls nest inside their parent", () => {
    renderVirtual(<StructuredView events={loadFixture("issue-to-pr")} />);
    for (const tool of ["Read", "Task", "Bash", "Edit"]) {
      expect(toolButton(tool), `no ${tool} card`).not.toBeNull();
    }
    expect(toolButton("Grep"), "the subagent's Grep call rendered at the top level").toBeNull();
    fireEvent.click(toolButton("Task")!);
    expect(toolButton("Grep"), "the Grep call is not nested in the Task card").not.toBeNull();
  });

  it("pairs a tool_call with its tool_result by tool_id", () => {
    renderVirtual(<StructuredView events={loadFixture("issue-to-pr")} />);
    const edit = toolButton("Edit");
    expect(edit, "no Edit card").not.toBeNull();
    fireEvent.click(edit!);
    expect(edit!.parentElement!.textContent, "the Edit card lacks its result").toContain("ok");
  });

  it("shows the denied Bash call's error result", () => {
    renderVirtual(<StructuredView events={loadFixture("issue-to-pr")} />);
    const bash = toolButton("Bash");
    expect(bash, "no Bash card").not.toBeNull();
    fireEvent.click(bash!);
    expect(bash!.parentElement!.textContent, "the Bash card lacks its error result").toContain("denied");
  });

  it("renders a thinking block collapsed", () => {
    const { container } = renderVirtual(<StructuredView events={loadFixture("issue-to-pr")} />);
    const details = container.querySelector("details");
    expect(details, "no thinking block").not.toBeNull();
    expect(details!.open).toBe(false);
    expect(details!.textContent).toContain("The issue asks for a CLI flag.");
  });

  it("marks turns by their turn_number", () => {
    renderVirtual(<StructuredView events={loadFixture("issue-to-pr")} />);
    expect(screen.queryAllByText("Turn 1"), "Turn 1 markers (one per run)").toHaveLength(4);
    expect(screen.queryAllByText("Turn 2"), "the developer run's Turn 2 marker").toHaveLength(1);
  });

  it("names the subagent at its start and its end, paired by tool_id", () => {
    renderVirtual(<StructuredView events={loadFixture("issue-to-pr")} />);
    expect(screen.queryByText("Explore Agent"), "no subagent start marker").not.toBeNull();
    expect(screen.queryByText("Subagent Explore completed"), "the end marker does not name the subagent").not.toBeNull();
  });

  it("paints a subagent's start marker from its identity row", () => {
    const events = [
      { type: "subagent_start", tool_id: "t-dev", name: "developer", timestamp: "2026-10-19T00:00:00Z" },
    ] as unknown as LogEvent[];
    const { container } = renderVirtual(<StructuredView events={events} />);
    expect(screen.queryByText("Developer Agent"), "the marker reads the display name").not.toBeNull();
    expect(container.querySelector('[data-avatar="smith"]'), "the marker carries the row's avatar").not.toBeNull();
    expect(container.querySelector("svg.sprite"), "a transcript marker is still, not bobbing").toHaveAttribute("data-frames", "1");
  });

  it("renders progress with its app and message", () => {
    renderVirtual(<StructuredView events={loadFixture("onboarding")} />);
    expect(screen.queryByText("[web] Booting web"), "no progress line").not.toBeNull();
  });

  it("renders an error with its message and code", () => {
    renderVirtual(<StructuredView events={loadFixture("issue-to-pr")} />);
    expect(screen.queryByText(/Tool denied by policy: Bash\(curl \*\)/)?.textContent, "the error line lacks its code").toContain("ERR_TOOL_DENIED");
  });

  it("keeps a call whose parent was lost at the top level", () => {
    const orphan: LogEvent = {
      type: "tool_call", timestamp: "", tool_id: "toolu_orphan", tool_name: "Glob", summary: "Glob *.py",
      tool_input: {}, parent_tool_use_id: "toolu_gone",
    };
    renderVirtual(<StructuredView events={[orphan]} />);
    expect(toolButton("Glob"), "the orphaned call was dropped").not.toBeNull();
  });

  it("a call whose result was lost is not Running... once the job is complete", () => {
    const lost: LogEvent = { type: "tool_call", timestamp: "", tool_id: "toolu_lost", tool_name: "Glob", summary: "", tool_input: {} };
    const { unmount } = renderVirtual(<StructuredView events={[lost]} isComplete={false} />);
    expect(screen.queryByText("Running..."), "an unanswered call on a live job is running").not.toBeNull();
    unmount();
    renderVirtual(<StructuredView events={[lost]} isComplete />);
    expect(screen.queryByText("Running..."), "a finished job's unanswered call still pulses Running...").toBeNull();
  });

  it("pre-contract job: the error line shows its text and the tool call renders", () => {
    renderVirtual(<StructuredView events={loadFixture("legacy-rows")} />);
    expect(screen.queryByText("legacy boom"), "the legacy error text is missing").not.toBeNull();
    const bash = toolButton("Bash");
    expect(bash, "the legacy tool call is missing").not.toBeNull();
    fireEvent.click(bash!);
    expect(bash!.parentElement!.textContent, "the legacy result did not pair via tool_use_id").toContain("total 0");
  });
});
