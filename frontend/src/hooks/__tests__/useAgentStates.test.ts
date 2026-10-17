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

  it("reads summary and tool_id only; legacy names are the wire normalizer's job", () => {
    const events = [
      { type: "node_started", node: "developer", attempt: 1, timestamp: ts },
      { type: "tool_call", node: "developer", tool_name: "Read", tool_id: "t2", summary: "a.py", input: "stale", tool_input: {}, timestamp: ts },
      { type: "tool_result", node: "developer", tool_use_id: "t2", content: "text", is_error: true, timestamp: ts },
    ] as unknown as LogEvent[];

    const { result } = renderHook(() => useAgentStates(events));
    const [developer] = result.current.agents;
    expect(developer.toolCalls[0]).toMatchObject({ tool_id: "t2", input: "a.py" });
    expect(developer.toolCalls[0].result, "a result keyed only by tool_use_id must not pair").toBeUndefined();
  });
});
