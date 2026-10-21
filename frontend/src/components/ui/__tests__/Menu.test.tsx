import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Dialog } from "../Dialog";
import { Menu } from "../Menu";

function items(onSelect = vi.fn()) {
  return [
    { label: "Rename", onSelect: () => onSelect("Rename") },
    { label: "Duplicate", onSelect: () => onSelect("Duplicate") },
    { label: "Delete", onSelect: () => onSelect("Delete"), destructive: true },
  ];
}

describe("Menu", () => {
  it("opens a named menu from its button and focuses the first item", async () => {
    const user = userEvent.setup();
    render(<Menu label="Actions for quick-fix" items={items()} />);
    const trigger = screen.getByRole("button", { name: "Actions for quick-fix" });
    expect(trigger).toHaveAttribute("aria-haspopup", "menu");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("menu")).toBeNull();
    await user.click(trigger);
    const menu = screen.getByRole("menu", { name: "Actions for quick-fix" });
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(trigger).toHaveAttribute("aria-controls", menu.id);
    expect(screen.getAllByRole("menuitem").map((el) => el.textContent)).toEqual(["Rename", "Duplicate", "Delete"]);
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("menuitem", { name: "Rename" })));
    expect(screen.getByRole("menuitem", { name: "Delete" })).toHaveClass("text-destructive");
  });

  it("selects with a click, closes and hands focus back to its button", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<Menu label="Actions" items={items(onSelect)} />);
    await user.click(screen.getByRole("button", { name: "Actions" }));
    await user.click(screen.getByRole("menuitem", { name: "Duplicate" }));
    expect(onSelect).toHaveBeenCalledWith("Duplicate");
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Actions" }));
  });

  it("moves with the arrow keys and closes on Escape, leaving the dialog around it open", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <Dialog onClose={onClose} title="Templates">
        <Menu label="Actions" items={items()} />
      </Dialog>,
    );
    await user.click(screen.getByRole("button", { name: "Actions" }));
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("menuitem", { name: "Rename" })));
    await user.keyboard("{ArrowDown}{ArrowDown}");
    expect(document.activeElement).toBe(screen.getByRole("menuitem", { name: "Delete" }));
    await user.keyboard("{ArrowDown}");
    expect(document.activeElement, "the arrows wrap").toBe(screen.getByRole("menuitem", { name: "Rename" }));
    await user.keyboard("{End}");
    expect(document.activeElement).toBe(screen.getByRole("menuitem", { name: "Delete" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Actions" }));
    expect(onClose, "Escape closed the menu, not the dialog").not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("closes on a press outside without selecting", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(
      <div>
        <button type="button">Elsewhere</button>
        <Menu label="Actions" items={items(onSelect)} />
      </div>,
    );
    await user.click(screen.getByRole("button", { name: "Actions" }));
    fireEvent.pointerDown(screen.getByRole("button", { name: "Elsewhere" }));
    expect(screen.queryByRole("menu")).toBeNull();
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("keeps its clicks from the row behind it", async () => {
    const user = userEvent.setup();
    const row = vi.fn();
    render(
      <div onClick={row}>
        <Menu label="Actions" items={items()} />
      </div>,
    );
    await user.click(screen.getByRole("button", { name: "Actions" }));
    await user.click(screen.getByRole("menuitem", { name: "Rename" }));
    expect(row, "neither the trigger nor an item click reaches the row").not.toHaveBeenCalled();
  });

  it("skips a disabled item", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<Menu label="Actions" items={[{ label: "Rename", onSelect, disabled: true }, { label: "Delete", onSelect }]} />);
    await user.click(screen.getByRole("button", { name: "Actions" }));
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("menuitem", { name: "Delete" })));
    expect(screen.getByRole("menuitem", { name: "Rename" })).toBeDisabled();
  });
});
