import { describe, expect, it } from "vitest";
import { render, renderHook, screen } from "@testing-library/react";
import { ToolCallStream } from "../ToolCallStream";
import { useAgentStates } from "@/hooks/useAgentStates";
import { loadFixture } from "@/lib/__tests__/fixtures";

function developerCalls() {
  return renderHook(() => useAgentStates(loadFixture("issue-to-pr"))).result.current.agents[1].toolCalls;
}

describe("ToolCallStream", () => {
  it("renders top-level calls and the subagent's nested call", () => {
    render(<ToolCallStream toolCalls={developerCalls()} />);
    for (const name of ["Task", "Bash", "Edit"]) expect(screen.getByText(name)).toBeInTheDocument();
    expect(screen.queryByText("Grep"), "the subagent's nested call must render").not.toBeNull();
    expect(screen.getByText("Grep argparse")).toBeInTheDocument();
    expect(screen.getAllByText("Explore"), "the subagent is named once, on its parent").toHaveLength(1);
  });

  it("marks the denied Bash call as an error", () => {
    render(<ToolCallStream toolCalls={developerCalls()} />);
    const bashRow = screen.getByText("curl https://example.test").parentElement!;
    expect(bashRow, "the denied call's row must carry the error marker").toHaveTextContent("✗");
    expect(screen.getAllByText("✗"), "only the denied call failed").toHaveLength(1);
  });
});
