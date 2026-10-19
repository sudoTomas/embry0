import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { AgentIndicator } from "../AgentIndicator";

describe("AgentIndicator", () => {
  it("draws the active agent as an icon with its label, never an emoji", () => {
    const { container } = render(<AgentIndicator agentType="triage" size="md" />);
    expect(container.querySelector("svg")).not.toBeNull();
    expect(screen.getByText("Triaging")).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/\p{Extended_Pictographic}/u);
  });

  it("keeps the label in the title when small", () => {
    const { container } = render(<AgentIndicator agentType="validator" />);
    expect(container.firstElementChild).toHaveAttribute("title", "Validating");
    expect(container.textContent).toBe("");
  });
});
