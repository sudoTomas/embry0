import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AgentIdentityFields } from "../AgentIdentityFields";

const value = { display_name: "Release Bot", tagline: "Ships it.", avatar: "rogue" as const, color: "cyan" as const };

describe("AgentIdentityFields", () => {
  it("offers the twelve avatars as a sprite grid, in the chosen colour, with the current one checked", () => {
    render(<AgentIdentityFields type="release-bot" value={value} onChange={() => {}} />);
    const grid = screen.getByRole("radiogroup", { name: "Avatar" });
    const options = grid.querySelectorAll('[role="radio"]');
    expect(options).toHaveLength(12);
    expect(grid.querySelector('[role="radio"][aria-checked="true"]')).toHaveAttribute("aria-label", "rogue");
    for (const svg of grid.querySelectorAll("svg")) expect(svg).toHaveAttribute("data-color", "cyan");
    expect(screen.queryByRole("combobox", { name: /avatar/i })).toBeNull();
  });

  it("names each avatar option once: the button carries the name, its sprite is decorative", () => {
    render(<AgentIdentityFields type="release-bot" value={value} onChange={() => {}} />);
    const option = screen.getByRole("radio", { name: "rogue" });
    const svg = option.querySelector("svg")!;
    expect(svg).toHaveAttribute("aria-hidden", "true");
    expect(svg).not.toHaveAttribute("aria-label");
  });

  it("reports a chosen avatar", async () => {
    const onChange = vi.fn();
    render(<AgentIdentityFields type="release-bot" value={value} onChange={onChange} />);
    await userEvent.click(screen.getByRole("radio", { name: "bard" }));
    expect(onChange).toHaveBeenCalledWith({ avatar: "bard" });
  });

  it("previews the chosen avatar and colour at 48 px", () => {
    render(<AgentIdentityFields type="release-bot" value={value} onChange={() => {}} />);
    const preview = screen.getByTitle("Release Bot").querySelector("svg")!;
    expect(preview).toHaveAttribute("data-avatar", "rogue");
    expect(preview).toHaveAttribute("data-color", "cyan");
    expect(preview).toHaveAttribute("width", "48");
  });
});
