import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { LevelBadge } from "../LevelBadge";

describe("LevelBadge", () => {
  it("shows the total level, says which skill set the tier, and links to the skill sheet", () => {
    render(
      <MemoryRouter>
        <LevelBadge agentType="blast-radius" totalLevel={1234} bestSkill="Coding" bestLevel={84} tier="rune" />
      </MemoryRouter>,
    );
    const badge = screen.getByTestId("level-badge");
    expect(badge).toHaveTextContent("Total level 1,234");
    expect(badge).toHaveAttribute("data-tier", "rune");
    expect(badge).toHaveAttribute("title", "Rune tier from Coding 84 · open the skill sheet");
    expect(badge).toHaveAttribute("href", "/achievements?character=blast-radius");
  });

  it("does not let the click reach the row around it", () => {
    let rowClicks = 0;
    render(
      <MemoryRouter>
        <div onClick={() => (rowClicks += 1)}>
          <LevelBadge agentType="developer" totalLevel={23} bestSkill="Coding" bestLevel={1} tier="bronze" />
        </div>
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByTestId("level-badge"));
    expect(rowClicks).toBe(0);
  });
});
