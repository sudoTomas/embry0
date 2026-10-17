import { describe, it, expect } from "vitest";
import { normalizeWireEvent, eventNode } from "../wire";

describe("normalizeWireEvent", () => {
  it("passes a contract event through unchanged", () => {
    const ev = { type: "text", text: "hi", timestamp: "2026-01-01T00:00:00Z", run_id: "r1", node: "developer", event_seq: 7 };
    expect(normalizeWireEvent(ev)).toEqual(ev);
  });

  it("maps a legacy error row's `error` to `message`", () => {
    const out = normalizeWireEvent({ type: "error", error: "boom", timestamp: "t" });
    expect(out).toMatchObject({ type: "error", message: "boom" });
    expect(out).not.toHaveProperty("error");
  });

  it("maps a legacy tool_call's string `input` to `summary`", () => {
    const out = normalizeWireEvent({
      type: "tool_call", tool_id: "t1", tool_name: "Bash", input: "ls -la",
      tool_input: { command: "ls -la" }, timestamp: "t",
    });
    expect(out).toMatchObject({ type: "tool_call", summary: "ls -la", tool_input: { command: "ls -la" } });
    expect(out).not.toHaveProperty("input");
  });

  it("keeps a contract summary over a legacy input when both are present", () => {
    const out = normalizeWireEvent({ type: "tool_call", tool_id: "t", tool_name: "Read", summary: "Read a.py", input: "x", tool_input: {}, timestamp: "t" });
    expect(out).toMatchObject({ summary: "Read a.py" });
  });

  it("maps tool_use_id to tool_id and node_id to node", () => {
    const out = normalizeWireEvent({ type: "tool_result", tool_use_id: "t1", content: "ok", is_error: false, node_id: "developer", timestamp: "t" });
    expect(out).toMatchObject({ tool_id: "t1", node: "developer" });
    expect(out).not.toHaveProperty("tool_use_id");
    expect(out).not.toHaveProperty("node_id");
  });

  it("turns a raw row into a system event with the text and no fake timestamp", () => {
    const out = normalizeWireEvent({ type: "raw", message: "not json", event_seq: 3 });
    expect(out).toEqual({ type: "system", message: "not json", timestamp: "", event_seq: 3 });
  });

  it("turns an unknown type into a system event carrying its JSON", () => {
    const out = normalizeWireEvent({ type: "pipeline_stage_completed", pr_url: "u", timestamp: "t", event_seq: 9 });
    expect(out.type).toBe("system");
    expect((out as { message: string }).message).toContain("pipeline_stage_completed");
    expect(out.event_seq).toBe(9);
  });

  it("rejects non-objects as a system event rather than throwing", () => {
    expect(normalizeWireEvent("garbage").type).toBe("system");
    expect(normalizeWireEvent(null).type).toBe("system");
  });
});

describe("eventNode", () => {
  it("reads node and returns empty when absent", () => {
    expect(eventNode({ type: "system", message: "", timestamp: "", node: "review" })).toBe("review");
    expect(eventNode({ type: "system", message: "", timestamp: "" })).toBe("");
  });
});
