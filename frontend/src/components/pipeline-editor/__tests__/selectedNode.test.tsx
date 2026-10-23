import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ReactFlowProvider, type NodeProps } from "@xyflow/react";

vi.mock("@/hooks/useAgents", () => ({
  useAgents: () => ({ data: [{ type: "developer", display_name: "Developer", avatar: "smith", color: "amber", skills: [] }] }),
}));

import { PartyNode } from "@/components/pipeline/PartyNode";
import { StartEndNode } from "../StartEndNode";

// A selected node is marked by a thin ring in its own colour, never a glow
// (a drop-shadow filter on the tile).
function props(data: Record<string, unknown>, selected: boolean): NodeProps {
  return { id: "n", data, selected } as unknown as NodeProps;
}

function noGlow(container: HTMLElement) {
  for (const el of container.querySelectorAll<HTMLElement>("*")) {
    expect(el.style?.filter ?? "").not.toContain("drop-shadow");
  }
}

describe("selected pipeline nodes", () => {
  it("rings a selected start node in its own colour, with no glow", () => {
    const { container } = render(
      <ReactFlowProvider>
        <StartEndNode {...props({ nodeRole: "start" }, true)} />
      </ReactFlowProvider>,
    );
    expect(screen.getByTestId("node-selected-ring")).toHaveAttribute("stroke", "currentColor");
    noGlow(container);
  });

  it("rings a selected agent node in its identity colour, with no glow", () => {
    const { container } = render(
      <ReactFlowProvider>
        <PartyNode {...props({ agentType: "developer", label: "developer" }, true)} />
      </ReactFlowProvider>,
    );
    const ring = screen.getByTestId("node-selected-ring");
    expect(ring.style.borderColor, "the ring takes the agent's hex").toBeTruthy();
    expect(ring.className).toContain("border");
    noGlow(container);
  });

  it("draws no ring on an unselected node", () => {
    render(
      <ReactFlowProvider>
        <PartyNode {...props({ agentType: "developer", label: "developer" }, false)} />
      </ReactFlowProvider>,
    );
    expect(screen.queryByTestId("node-selected-ring")).toBeNull();
  });
});
