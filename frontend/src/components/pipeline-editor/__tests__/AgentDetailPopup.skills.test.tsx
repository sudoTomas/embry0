import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import type { Node } from "@xyflow/react";

vi.mock("@/hooks/useAgents", () => ({
  useAgents: () => ({ data: [{ type: "developer", model: "m", skills: ["superpowers:brainstorming", "local:house-style"], display_name: "Developer", avatar: "smith", color: "amber" }] }),
}));
vi.mock("@/hooks/useSkills", () => ({
  useSkills: () => ({ data: [
    { name: "superpowers:brainstorming", source_id: "superpowers", description: "", available: true, status: "ready" },
    { name: "superpowers:writing-plans", source_id: "superpowers", description: "", available: true, status: "ready" },
    { name: "local:house-style", source_id: "local", description: "", available: true, status: "ready" },
  ], isLoading: false }),
}));
import { AgentDetailPopup } from "../AgentDetailPopup";

function node(skills: unknown): Node {
  return { id: "d", type: "agentNode", position: { x: 0, y: 0 }, data: { agentType: "developer", label: "Developer", skills } };
}
function setup(skills: unknown) {
  const onUpdate = vi.fn();
  render(<MemoryRouter><AgentDetailPopup node={node(skills)} onUpdate={onUpdate} onClose={() => {}} /></MemoryRouter>);
  return { onUpdate, user: userEvent.setup() };
}

describe("AgentDetailPopup skills delta", () => {
  it("shows added, removed and the effective set", async () => {
    const { user } = setup({ add: ["superpowers:writing-plans"], remove: ["local:house-style"] });
    await user.click(screen.getByRole("button", { name: /edit/i }));
    // SkillPicker shows a selected name twice (its pill and the dimmed catalog row): assert the pill.
    expect(within(screen.getByTestId("skills-added")).getByRole("button", { name: "Remove superpowers:writing-plans" })).toBeInTheDocument();
    expect(within(screen.getByTestId("skills-removed")).getByText("local:house-style")).toBeInTheDocument();
    const effective = screen.getByTestId("skills-effective");
    expect(effective).toHaveTextContent("superpowers:brainstorming");
    expect(effective).toHaveTextContent("superpowers:writing-plans");
    expect(effective).not.toHaveTextContent("local:house-style");
  });
  it("removing a definition skill writes it into remove", async () => {
    const { onUpdate, user } = setup(null);
    await user.click(screen.getByRole("button", { name: /edit/i }));
    await user.click(screen.getByRole("button", { name: "Remove local:house-style from this node" }));
    expect(onUpdate).toHaveBeenCalledWith({ skills: { add: [], remove: ["local:house-style"] } });
  });
  it("a legacy replace delta shows every definition skill as removed and can be kept", async () => {
    const { onUpdate, user } = setup({ add: ["superpowers:writing-plans"], remove: "*" });
    await user.click(screen.getByRole("button", { name: /edit/i }));
    expect(screen.getByTestId("skills-removed")).toHaveTextContent(/all of the definition's skills/i);
    await user.click(screen.getByRole("button", { name: /keep the definition's skills/i }));
    expect(onUpdate).toHaveBeenCalledWith({ skills: { add: ["superpowers:writing-plans"], remove: [] } });
  });
  it("clearing the last change stores null", async () => {
    const { onUpdate, user } = setup({ add: ["superpowers:writing-plans"], remove: [] });
    await user.click(screen.getByRole("button", { name: /edit/i }));
    await user.click(screen.getByRole("button", { name: "Remove superpowers:writing-plans" }));
    expect(onUpdate).toHaveBeenCalledWith({ skills: null });
  });
  it("has no per-skill mode select", async () => {
    const { user } = setup({ add: ["superpowers:writing-plans"], remove: [] });
    await user.click(screen.getByRole("button", { name: /edit/i }));
    expect(screen.queryByRole("option", { name: "Autonomous" })).toBeNull();
  });
});
