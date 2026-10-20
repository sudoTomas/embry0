import { render, screen } from "@testing-library/react";
import type { NodeProps } from "@xyflow/react";
import { describe, expect, it, vi } from "vitest";
import css from "../party.css?raw";

// Handle reads the node context React Flow provides; outside a canvas it has none.
vi.mock("@xyflow/react", async (importOriginal) => ({ ...(await importOriginal<typeof import("@xyflow/react")>()), Handle: () => null }));
vi.mock("@/hooks/useAgents", () => ({
  useAgents: () => ({
    data: [
      { type: "developer", display_name: "Developer", avatar: "smith", color: "amber", skills: ["superpowers:tdd", "superpowers:debug"] },
      { type: "review", display_name: "Reviewer", avatar: "warden", color: "rose", skills: [] },
    ],
  }),
}));
import { PartyNode } from "../PartyNode";

function props(data: Record<string, unknown>, selected = false): NodeProps {
  return { id: "n", data, selected, type: "agentNode", dragging: false, zIndex: 0, isConnectable: false, positionAbsoluteX: 0, positionAbsoluteY: 0, deletable: true, draggable: true, selectable: true, width: 96, height: 96 } as unknown as NodeProps;
}
const wrap = render;

describe("PartyNode, editor mode", () => {
  it("shows the idle sprite, the nameplate and the effective skill count with the model", () => {
    wrap(<PartyNode {...props({ agentType: "developer", label: "developer", model: "claude-opus-4", skills: { add: ["superpowers:plan"], remove: ["superpowers:debug"] } })} />);
    const node = screen.getByTestId("party-node");
    expect(node).toHaveAttribute("data-mode", "editor");
    const sprite = node.querySelector("svg.sprite")!;
    expect(sprite).toHaveAttribute("data-avatar", "smith");
    expect(sprite).toHaveAttribute("data-state", "idle");
    expect(sprite).toHaveAttribute("width", "48");
    expect(node.querySelector(".sprite-nameplate")).toHaveTextContent("Developer");
    expect(node.querySelector(".hud")).toHaveTextContent("2 skills · opus");
    expect(node.querySelector("[data-ring]")).toHaveStyle({ borderColor: "#fbbf24" });
  });

  it("resolves the reviewer spelling to the review row", () => {
    wrap(<PartyNode {...props({ agentType: "reviewer", label: "reviewer", model: null, skills: null })} />);
    expect(screen.getByTestId("party-node").querySelector(".sprite-nameplate")).toHaveTextContent("Reviewer");
    expect(screen.getByTestId("party-node").querySelector(".hud")).toHaveTextContent("0 skills · defaults");
  });

  it("shows the slug and the golem for a type with no row", () => {
    wrap(<PartyNode {...props({ agentType: "release-bot", label: "release", model: null, skills: null })} />);
    const node = screen.getByTestId("party-node");
    expect(node.querySelector(".sprite-nameplate")).toHaveTextContent("release-bot");
    expect(node.querySelector("svg.sprite")).toHaveAttribute("data-avatar", "golem");
  });
});

describe("PartyNode, live mode", () => {
  const live = (state: string, extra: Record<string, unknown> = {}) =>
    props({ agentType: "developer", label: "developer", mode: "live", state, costUsd: 1.5, turns: 12, iteration: 2, ...extra });

  it("maps the node state to the sprite and the ring", () => {
    const { unmount } = wrap(<PartyNode {...live("pending")} />);
    let node = screen.getByTestId("party-node");
    expect(node).toHaveAttribute("data-mode", "live");
    expect(node.querySelector("svg.sprite")).toHaveAttribute("data-state", "pending");
    expect(node.querySelector("[data-ring]")).toHaveAttribute("data-ring", "pending");
    unmount();
    wrap(<PartyNode {...live("running")} />);
    node = screen.getByTestId("party-node");
    expect(node.querySelector("svg.sprite")).toHaveAttribute("data-state", "thinking");
    expect(node.querySelector("[data-ring]")).toHaveAttribute("data-ring", "running");
  });

  it("writes attempt, cost and turns on the HUD line", () => {
    wrap(<PartyNode {...live("completed")} />);
    const node = screen.getByTestId("party-node");
    expect(node.querySelector(".hud")).toHaveTextContent("#2 · $1.50 · 12 turns");
    expect(node.querySelector("svg.sprite")).toHaveAttribute("data-state", "done");
    expect(node.querySelector("[data-ring]")).toHaveAttribute("data-ring", "completed");
  });

  it("shows failed as failed", () => {
    wrap(<PartyNode {...live("failed")} />);
    expect(screen.getByTestId("party-node").querySelector("svg.sprite")).toHaveAttribute("data-state", "failed");
  });
});

describe("party.css", () => {
  it("flashes the ring on completion only under prefers-reduced-motion: no-preference", () => {
    const gatedAt = css.indexOf("@media (prefers-reduced-motion: no-preference)");
    expect(gatedAt).toBeGreaterThan(-1);
    expect(css.slice(0, gatedAt)).not.toContain("animation:");
    expect(css.slice(gatedAt)).toMatch(/\[data-ring="completed"\]\s*\{\s*animation:\s*scale-in/);
  });
  it("hard-codes no hex colour", () => {
    expect(css).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  });
});
