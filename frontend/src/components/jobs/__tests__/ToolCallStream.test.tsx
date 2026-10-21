import { describe, expect, it } from "vitest";
import { render, renderHook, screen } from "@testing-library/react";
import { ToolCallStream } from "../ToolCallStream";
import { useAgentStates, type ToolCallEntry } from "@/hooks/useAgentStates";
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

  it("shows the calls of its first paint at once", () => {
    const { container } = render(<ToolCallStream toolCalls={developerCalls()} />);
    expect(container.querySelector(".tool-call-arrive"), "nothing on the first paint fades in").toBeNull();
  });

  it("fades in a call that streams in after the first paint, nested calls too", () => {
    const first = developerCalls();
    const { rerender } = render(<ToolCallStream toolCalls={first} />);
    const call = (tool_id: string, input: string): ToolCallEntry => ({ tool_name: "Read", tool_id, input, children: [] });
    const [task, ...rest] = first;
    rerender(
      <ToolCallStream
        toolCalls={[{ ...task, children: [...task.children, call("live-nested", "Read the nested file")] }, ...rest, call("live-top", "Read the next file")]}
      />,
    );
    const arriving = (text: string) => screen.getByText(text).closest(".tool-call-arrive");
    expect(arriving("Read the next file"), "a call that streamed in fades in").not.toBeNull();
    expect(arriving("Read the nested file"), "a subagent's call that streamed in fades in").not.toBeNull();
    expect(arriving("Grep argparse"), "a call of the first paint stays as it was").toBeNull();
    expect(arriving("curl https://example.test")).toBeNull();
  });
});
