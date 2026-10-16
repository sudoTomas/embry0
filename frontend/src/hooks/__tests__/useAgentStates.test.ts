import { describe, expect, it } from "vitest";
import { renderHook } from "@testing-library/react";
import { useAgentStates } from "../useAgentStates";
import type { LogEvent } from "@/lib/types";

const ts = "2026-10-15T00:00:00Z";

describe("useAgentStates tool-call pairing", () => {
  it("reads the contract names (summary, tool_id) and pairs the result with its call", () => {
    const events = [
      { type: "node_started", node: "developer", attempt: 1, timestamp: ts },
      {
        type: "tool_call",
        node: "developer",
        tool_name: "Bash",
        tool_id: "t1",
        summary: "ls -la",
        tool_input: { command: "ls -la" },
        timestamp: ts,
      },
      { type: "tool_result", node: "developer", tool_id: "t1", content: "ok", is_error: false, timestamp: ts },
    ] as unknown as LogEvent[];

    const { result } = renderHook(() => useAgentStates(events));
    const [developer] = result.current.agents;
    expect(developer.toolCalls).toHaveLength(1);
    expect(developer.toolCalls[0]).toMatchObject({ tool_id: "t1", input: "ls -la", result: "ok", is_error: false });
  });

  it("still reads the legacy names (input, tool_use_id) for events persisted before the contract", () => {
    const events = [
      { type: "node_started", node: "developer", timestamp: ts },
      { type: "tool_call", node: "developer", tool_name: "Read", tool_id: "t2", input: { path: "a.py" }, timestamp: ts },
      { type: "tool_result", node: "developer", tool_use_id: "t2", content: "text", is_error: true, timestamp: ts },
    ] as unknown as LogEvent[];

    const { result } = renderHook(() => useAgentStates(events));
    const [developer] = result.current.agents;
    expect(developer.toolCalls[0]).toMatchObject({ tool_id: "t2", input: '{"path":"a.py"}', result: "text", is_error: true });
  });
});
