import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { LevelBadge } from "../LevelBadge";

describe("LevelBadge", () => {
  it("shows the skill and level, and says the tier and the next level in its title", () => {
    render(<LevelBadge skill="Smithing" level={84} tier="rune" xpToNext={1234} />);
    const badge = screen.getByTestId("level-badge");
    expect(badge).toHaveTextContent("Smithing 84");
    expect(badge).toHaveAttribute("data-tier", "rune");
    expect(badge).toHaveAttribute("title", "Rune tier · 1,234 XP to level 85");
  });

  it("says level 99 when there is nothing left to the next level", () => {
    render(<LevelBadge skill="Mining" level={99} tier="divine" xpToNext={null} />);
    expect(screen.getByTestId("level-badge")).toHaveAttribute("title", "Divine tier · level 99");
  });
});
