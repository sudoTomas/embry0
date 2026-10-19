import { describe, expect, it } from "vitest";
import type { LogEvent } from "@/lib/types";
import { stateFor, stateForNode } from "../spriteState";

const call = (id: string): LogEvent => ({ type: "tool_call", tool_name: "Edit", tool_id: id, summary: "" } as unknown as LogEvent);
const result = (id: string): LogEvent => ({ type: "tool_result", tool_id: id, content: "" } as unknown as LogEvent);
const text = (): LogEvent => ({ type: "text", text: "…" } as unknown as LogEvent);

describe("stateFor (a run)", () => {
  it("maps the terminal and waiting statuses directly", () => {
    expect(stateFor({ status: "pending", events: [] })).toBe("pending");
    expect(stateFor({ status: "completed", events: [call("1")] })).toBe("done");
    expect(stateFor({ status: "failed", events: [] })).toBe("failed");
  });
  it("is tool while the newest tool call has no result, thinking otherwise", () => {
    expect(stateFor({ status: "running", events: [] })).toBe("thinking");
    expect(stateFor({ status: "running", events: [call("1")] })).toBe("tool");
    expect(stateFor({ status: "running", events: [call("1"), result("1")] })).toBe("thinking");
    expect(stateFor({ status: "running", events: [call("1"), result("1"), call("2")] })).toBe("tool");
    expect(stateFor({ status: "running", events: [call("1"), text()] })).toBe("tool");
    expect(stateFor({ status: "running", events: [call("1"), result("1"), text()] })).toBe("thinking");
  });
  it("matches results to calls by tool_id", () => {
    expect(stateFor({ status: "running", events: [call("1"), call("2"), result("1")] })).toBe("tool");
    expect(stateFor({ status: "running", events: [call("1"), result("x")] })).toBe("tool");
    expect(stateFor({ status: "running", events: [result("1"), call("1")] })).toBe("tool");
  });
});

describe("stateForNode (a graph node)", () => {
  it("maps the live node vocabulary, with ready and unknown as pending", () => {
    expect(stateForNode("pending")).toBe("pending");
    expect(stateForNode("ready")).toBe("pending");
    expect(stateForNode(undefined)).toBe("pending");
    expect(stateForNode("running")).toBe("thinking");
    expect(stateForNode("completed")).toBe("done");
    expect(stateForNode("failed")).toBe("failed");
  });
});
