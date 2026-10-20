import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { AgentFilterTabs } from "../AgentFilterTabs";
import type { NodeLiveState } from "@/lib/pipeline-live";
import { AGENT_COLORS } from "@/lib/agentVisuals";
import css from "@/index.css?raw";

function node(node_id: string, agent_type: string): NodeLiveState {
  return { node_id, agent_type, state: "completed", iteration: 1, cost_usd: 0, turns: 0, duration_seconds: 0 };
}

const NODES: Record<string, NodeLiveState> = {
  triage: node("triage", "triage"),
  developer: node("developer", "developer"),
};

const GOLD = /(^|\s)(bg|text|border)-primary(\/\d+)?(\s|$)/;

/** The palette colour a text class draws in, through a role token if it names one. */
function paletteOf(cls: string): string | undefined {
  const name = cls.match(/^text-([a-z-]+)$/)?.[1];
  if (!name) return undefined;
  const token = css.match(new RegExp(`--color-${name}:\\s*var\\(--color-([a-z]+)\\);`));
  return token ? token[1] : name;
}

/** A job page agent card's node, to the agent type its chip carries. */
const CHIP_TYPE: Record<string, string> = { triage: "triage", developer: "developer", review: "reviewer" };

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

  // Each agent reads in its role's colour, as the job page's agent cards and
  // the pipeline graph draw it, so a chip names its agent at a glance.
  it("names each unselected agent in the colour its card on the job page reads", () => {
    const nodes = Object.fromEntries(
      Object.values(CHIP_TYPE).map((type) => [type, node(type, type)]),
    );
    render(<AgentFilterTabs nodeStates={nodes} selectedNodeId={null} onSelect={vi.fn()} />);
    for (const [card, type] of Object.entries(CHIP_TYPE)) {
      const label = screen.getByText(type.charAt(0).toUpperCase() + type.slice(1));
      const want = paletteOf(AGENT_COLORS[card].text);
      expect(want, `${card}'s card reads no palette colour`).toBeTruthy();
      expect(paletteOf(label.className), `the ${type} chip reads "${label.className}", its card ${want}`).toBe(want);
    }
  });

  it("keeps an agent with no role colour in the chip's muted text", () => {
    render(
      <AgentFilterTabs
        nodeStates={{ ...NODES, output: node("output", "output") }}
        selectedNodeId={null}
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByText("Output").className).toBe("");
    expect(screen.getByRole("button", { name: /Output/ }).className).toContain("text-muted-foreground");
  });

  it("draws the selected agent neutral, without its role colour", () => {
    render(<AgentFilterTabs nodeStates={NODES} selectedNodeId="developer" onSelect={vi.fn()} />);
    expect(screen.getByText("Developer").className).not.toMatch(/text-role-/);
    expect(screen.getByText("Triage").className).toBe("text-role-triage");
  });
});
