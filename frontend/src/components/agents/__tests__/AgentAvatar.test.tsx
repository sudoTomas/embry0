import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { identityOf } from "@/lib/agentIdentity";
import { AgentAvatar } from "../AgentAvatar";

describe("AgentAvatar", () => {
  it("renders the agent's sprite with the roster id, the palette id and the display name", () => {
    render(<AgentAvatar identity={identityOf({ type: "developer" })} />);
    const tile = screen.getByTitle("Developer");
    expect(tile).toHaveAttribute("data-avatar", "smith");
    expect(tile).toHaveAttribute("data-color", "amber");
    const svg = tile.querySelector("svg")!;
    expect(svg).toHaveAttribute("data-avatar", "smith");
    expect(svg).toHaveAttribute("data-state", "idle");
    expect(svg).toHaveAttribute("aria-label", "Developer, idle");
    expect(tile.textContent).toBe("");
  });

  it("passes the state and size through", () => {
    render(<AgentAvatar identity={identityOf("qa")} state="tool" size={48} />);
    const svg = screen.getByTitle("QA").querySelector("svg")!;
    expect(svg).toHaveAttribute("data-state", "tool");
    expect(svg).toHaveAttribute("width", "48");
  });

  it("forwards still to the sprite", () => {
    render(<AgentAvatar identity={identityOf("qa")} state="thinking" still />);
    const svg = screen.getByTitle("QA").querySelector("svg")!;
    expect(svg).toHaveAttribute("data-frames", "1");
    expect(svg.querySelectorAll("g.sprite-frame")).toHaveLength(1);
  });

  it("rounds an odd size down to the nearest sprite size", () => {
    render(<AgentAvatar identity={identityOf("qa")} size={40} />);
    expect(screen.getByTitle("QA").querySelector("svg")).toHaveAttribute("width", "32");
  });
});
