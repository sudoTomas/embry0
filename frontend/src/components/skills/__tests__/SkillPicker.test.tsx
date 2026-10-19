import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { SkillPicker } from "../SkillPicker";

const CATALOG = [
  { name: "superpowers:writing-plans", source_id: "superpowers", description: "Write implementation plans", available: true, status: "ready" },
  { name: "superpowers:brainstorming", source_id: "superpowers", description: "Refine an idea", available: false, status: "syncing" },
  { name: "local:house-style", source_id: "local", description: "House style", available: true, status: "ready" },
];

function setup(value: string[] = []) {
  const onChange = vi.fn();
  render(<MemoryRouter><SkillPicker value={value} onChange={onChange} catalog={CATALOG} /></MemoryRouter>);
  return { onChange, user: userEvent.setup() };
}

describe("SkillPicker", () => {
  it("filters the catalog and adds a row on click", async () => {
    const { onChange, user } = setup();
    await user.type(screen.getByRole("combobox", { name: /search skills/i }), "plans");
    expect(screen.queryByText("local:house-style")).toBeNull();
    await user.click(screen.getByRole("option", { name: /superpowers:writing-plans/ }));
    expect(onChange).toHaveBeenCalledWith(["superpowers:writing-plans"]);
  });
  it("shows availability", () => {
    setup();
    expect(screen.getByRole("option", { name: /superpowers:brainstorming/ })).toHaveTextContent("syncing");
  });
  it("refuses an unknown catalog-shaped name", async () => {
    const { onChange, user } = setup();
    await user.type(screen.getByRole("combobox", { name: /search skills/i }), "superpowers:writing-plan{Enter}");
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent('Unknown skill "superpowers:writing-plan"');
  });
  it("accepts a repository skill without a colon", async () => {
    const { onChange, user } = setup();
    await user.type(screen.getByRole("combobox", { name: /search skills/i }), "commit-standard{Enter}");
    expect(onChange).toHaveBeenCalledWith(["commit-standard"]);
  });
  it("removes a selected pill and never duplicates", async () => {
    const { onChange, user } = setup(["local:house-style"]);
    await user.click(screen.getByRole("option", { name: /local:house-style/ }));
    expect(onChange).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Remove local:house-style" }));
    expect(onChange).toHaveBeenCalledWith([]);
  });
  it("links to the Skills page", () => {
    setup();
    expect(screen.getByRole("link", { name: /skills page/i })).toHaveAttribute("href", "/skills");
  });
});
