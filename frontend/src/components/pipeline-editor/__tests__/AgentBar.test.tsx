import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/hooks/useAgents", () => ({
  useAgents: () => ({
    data: [
      { type: "triage", display_name: "Triage", avatar: "seer", color: "violet" },
      { type: "developer", display_name: "Developer", avatar: "smith", color: "amber" },
      { type: "release-bot", display_name: "Release Bot", avatar: "rogue", color: "cyan", tagline: "Ships it." },
      { type: "proposal-brief", display_name: "Proposal Brief", avatar: "apprentice", color: "sky", runs_in_jobs: false },
    ],
  }),
}));
import { AgentBar } from "../AgentBar";

describe("AgentBar", () => {
  it("offers every agent row except triage, plus output, and links to the new-agent form", () => {
    render(<MemoryRouter><AgentBar nodes={[]} edges={[]} /></MemoryRouter>);
    expect(screen.queryByText("Triage")).toBeNull();
    expect(screen.getByText("Developer")).toBeInTheDocument();
    expect(screen.getByText("Release Bot")).toBeInTheDocument();
    expect(screen.getByText("output")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /new agent/i })).toHaveAttribute("href", "/agents/new");
    expect(screen.queryByText(/custom agent/i)).toBeNull();
  });

  it("offers no definition a job cannot run: a brief writer's voice is no step (#166)", () => {
    render(<MemoryRouter><AgentBar nodes={[]} edges={[]} /></MemoryRouter>);
    expect(screen.queryByText("Proposal Brief"), "runs_in_jobs false is no pipeline step").toBeNull();
  });
});
