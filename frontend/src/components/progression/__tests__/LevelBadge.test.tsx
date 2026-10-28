import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { LevelBadge } from "../LevelBadge";
import { PACK } from "./fixtures";

describe("LevelBadge", () => {
  it("shows the level, gives the total level and the skill that set the tier, and links to the skill sheet", () => {
    render(
      <MemoryRouter>
        <LevelBadge agentType="blast-radius" level={96} totalLevel={1234} bestSkill="Coding" bestLevel={84} tier="obsidian" pack={PACK} />
      </MemoryRouter>,
    );
    const badge = screen.getByTestId("level-badge");
    expect(badge).toHaveTextContent("Level 96");
    expect(badge).toHaveAttribute("data-tier", "obsidian");
    expect(badge).toHaveAttribute("title", "Total level 1,234 · Obsidian tier from Coding 84 · open the skill sheet");
    expect(badge).toHaveAttribute("href", "/achievements?character=blast-radius");
  });

  it("does not let the click reach the row around it", () => {
    let rowClicks = 0;
    render(
      <MemoryRouter>
        <div onClick={() => (rowClicks += 1)}>
          <LevelBadge agentType="developer" level={1} totalLevel={23} bestSkill="Coding" bestLevel={1} tier="bronze" pack={PACK} />
        </div>
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByTestId("level-badge"));
    expect(rowClicks).toBe(0);
  });
});
