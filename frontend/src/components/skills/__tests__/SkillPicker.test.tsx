import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { Dialog } from "@/components/ui/Dialog";
import { SkillPicker } from "../SkillPicker";

const CATALOG = [
  { name: "superpowers:writing-plans", source_id: "superpowers", description: "Write implementation plans", available: true, status: "ready" },
  { name: "superpowers:brainstorming", source_id: "superpowers", description: "Refine an idea", available: false, status: "syncing" },
  { name: "local:house-style", source_id: "local", description: "House style", available: true, status: "ready" },
];

function setup(value: string[] = []) {
  const onChange = vi.fn();
  render(<MemoryRouter><SkillPicker value={value} onChange={onChange} catalog={CATALOG} /></MemoryRouter>);
  return { onChange, user: userEvent.setup({ delay: null }) };
}

describe("SkillPicker", () => {
  it("filters the catalog and adds a row on click", async () => {
    const { onChange, user } = setup();
    await user.type(screen.getByRole("combobox", { name: /search skills/i }), "plans");
    expect(screen.queryByText("local:house-style")).toBeNull();
    await user.click(screen.getByRole("option", { name: /superpowers:writing-plans/ }));
    expect(onChange).toHaveBeenCalledWith(["superpowers:writing-plans"]);
  });
  it("ArrowDown then Enter adds the first filtered option", async () => {
    const { onChange, user } = setup();
    const box = screen.getByRole("combobox", { name: /search skills/i });
    await user.type(box, "superpowers");
    await user.keyboard("{ArrowDown}");
    const first = screen.getByRole("option", { name: /superpowers:writing-plans/ });
    expect(first).toHaveAttribute("aria-selected", "true");
    expect(box).toHaveAttribute("aria-activedescendant", first.id);
    await user.keyboard("{Enter}");
    expect(onChange).toHaveBeenCalledWith(["superpowers:writing-plans"]);
  });
  it("ArrowDown, ArrowUp and Escape move and clear the highlight", async () => {
    const { user } = setup();
    const box = screen.getByRole("combobox", { name: /search skills/i });
    await user.click(box);
    await user.keyboard("{ArrowDown}{ArrowDown}");
    expect(screen.getByRole("option", { name: /superpowers:brainstorming/ })).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{ArrowUp}");
    expect(screen.getByRole("option", { name: /superpowers:writing-plans/ })).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{Escape}");
    expect(box).not.toHaveAttribute("aria-activedescendant");
    for (const o of screen.getAllByRole("option")) expect(o).toHaveAttribute("aria-selected", "false");
  });
  it("Escape with a highlight clears it and stays inside; without one it reaches the dialog around it", async () => {
    const onClose = vi.fn();
    const user = userEvent.setup({ delay: null });
    render(
      <MemoryRouter>
        <Dialog onClose={onClose} title="Developer">
          <SkillPicker value={[]} onChange={vi.fn()} catalog={CATALOG} />
        </Dialog>
      </MemoryRouter>,
    );
    // The dialog's focus trap moves focus in on a timeout; let it land first.
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "Close" })));
    const box = screen.getByRole("combobox", { name: /search skills/i });
    await user.click(box);
    await user.keyboard("{ArrowDown}");
    expect(box).toHaveAttribute("aria-activedescendant");
    await user.keyboard("{Escape}");
    expect(box).not.toHaveAttribute("aria-activedescendant");
    expect(onClose, "that Escape was the picker's: it cleared a highlight").not.toHaveBeenCalled();
    await user.keyboard("{Escape}");
    expect(onClose, "with nothing to clear, Escape closes the dialog").toHaveBeenCalledTimes(1);
  });

  it("names the listbox only while it is rendered", () => {
    render(<MemoryRouter><SkillPicker value={[]} onChange={vi.fn()} catalog={CATALOG} isLoading /></MemoryRouter>);
    expect(screen.getByRole("combobox", { name: /search skills/i })).not.toHaveAttribute("aria-controls");
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
