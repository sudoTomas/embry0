import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { AgentFilterTabs } from "../AgentFilterTabs";
import type { NodeLiveState } from "@/lib/pipeline-live";

function node(node_id: string, agent_type: string): NodeLiveState {
  return { node_id, agent_type, state: "completed", iteration: 1, cost_usd: 0, turns: 0, duration_seconds: 0 };
}

const NODES: Record<string, NodeLiveState> = {
  triage: node("triage", "triage"),
  developer: node("developer", "developer"),
};

const GOLD = /(^|\s)(bg|text|border)-primary(\/\d+)?(\s|$)/;

describe("AgentFilterTabs", () => {
  // A selected chip is a selection, not the one thing on the view that needs
  // a person: it reads on the overlay in the full foreground, as the
  // sidebar's selected item does, without the gold.
  it("shows the selected All chip on the overlay in the full foreground, not gold", () => {
    render(<AgentFilterTabs nodeStates={NODES} selectedNodeId={null} onSelect={vi.fn()} />);
    const all = screen.getByRole("button", { name: "All" });
    expect(all.className).toContain("bg-overlay");
    expect(all.className).toContain("text-foreground");
    expect(all.className).not.toMatch(GOLD);
  });

  it("shows a selected agent chip the same way", () => {
    render(<AgentFilterTabs nodeStates={NODES} selectedNodeId="developer" onSelect={vi.fn()} />);
    const chip = screen.getByRole("button", { name: /Developer/ });
    expect(chip.className).toContain("bg-overlay");
    expect(chip.className).toContain("text-foreground");
    expect(chip.className).not.toMatch(GOLD);
  });
});
