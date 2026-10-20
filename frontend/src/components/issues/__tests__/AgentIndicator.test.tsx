import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/hooks/useAgents", () => ({ useAgents: () => ({ data: undefined, isLoading: false }) }));
import { AgentIndicator } from "../AgentIndicator";

describe("AgentIndicator", () => {
  it("shows the agent's thinking sprite from its identity, resolving the reviewer spelling", () => {
    const { container } = render(<AgentIndicator agentType="reviewer" />);
    const sprite = container.querySelector("svg.sprite")!;
    expect(sprite, "a sprite, not an emoji").not.toBeNull();
    expect(sprite).toHaveAttribute("data-avatar", "warden");
    expect(sprite).toHaveAttribute("data-state", "thinking");
    expect(container.textContent).toBe("");
  });

  it("names the agent beside the sprite at the md size", () => {
    const { container } = render(<AgentIndicator agentType="triage" size="md" />);
    expect(container.querySelector("svg.sprite")).toHaveAttribute("data-avatar", "seer");
    expect(container.textContent).toBe("Triage");
  });

  it("shows the fallback identity for a type it does not know, not a developer stand-in", () => {
    const { container } = render(<AgentIndicator agentType="release-bot" size="md" />);
    expect(container.querySelector("svg.sprite")).toHaveAttribute("data-avatar", "golem");
    expect(container.textContent).toBe("release-bot");
    expect(container.textContent).not.toContain("Developing");
  });
});
