import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { PRESET_IDS } from "@/components/roster/appearance";
import { AVATARS } from "@/lib/agentIdentity";
import { AgentIdentityFields } from "../AgentIdentityFields";

const value = { display_name: "Release Bot", tagline: "Ships it.", avatar: "rogue" as const, color: "cyan" as const };

describe("AgentIdentityFields", () => {
  it("offers every avatar as a sprite grid, in the chosen colour, with the current one checked", () => {
    render(<AgentIdentityFields type="release-bot" value={value} onChange={() => {}} />);
    const grid = screen.getByRole("radiogroup", { name: "Avatar" });
    const options = grid.querySelectorAll('[role="radio"]');
    expect(options).toHaveLength(AVATARS.length);
    expect(options.length).toBeGreaterThanOrEqual(12 + 16);
    expect(grid.querySelector('[role="radio"][aria-checked="true"]')).toHaveAttribute("aria-label", "rogue");
    for (const svg of grid.querySelectorAll("svg")) expect(svg).toHaveAttribute("data-color", "cyan");
    expect(screen.queryByRole("combobox", { name: /avatar/i })).toBeNull();
  });

  it("lists the adventurer presets after the legacy roster, each drawn at 32x32", () => {
    render(<AgentIdentityFields type="release-bot" value={value} onChange={() => {}} />);
    const grid = screen.getByRole("radiogroup", { name: "Avatar" });
    const names = [...grid.querySelectorAll('[role="radio"]')].map((b) => b.getAttribute("aria-label"));
    expect(names.slice(12)).toEqual(PRESET_IDS);
    const svg = screen.getByRole("radio", { name: "dragon-warrior" }).querySelector("svg")!;
    expect(svg).toHaveAttribute("viewBox", "0 0 32 32");
    expect(svg).toHaveAttribute("data-color", "cyan");
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
