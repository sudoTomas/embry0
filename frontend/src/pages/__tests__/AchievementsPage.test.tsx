import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { progression } from "@/components/progression/__tests__/fixtures";

const mockUseProgression = vi.fn();
vi.mock("@/hooks/useProgression", () => ({ useProgression: () => mockUseProgression() }));
vi.mock("@/hooks/useAgentIdentity", () => ({
  useIdentityLookup: () => (type: string) => ({ displayName: type === "developer" ? "Developer" : type }),
}));

import { AchievementsPage } from "../AchievementsPage";

function renderPage() {
  return render(
    <MemoryRouter>
      <AchievementsPage />
    </MemoryRouter>,
  );
}

describe("AchievementsPage", () => {
  it("shows the total level, every skill with who trains it, and the diary", () => {
    mockUseProgression.mockReturnValue({ data: progression() });
    renderPage();
    expect(screen.getByTestId("total-level")).toHaveTextContent("Total level 937 · 31,000,000 XP · 1 of 3 achievements unlocked");
    const tiles = screen.getAllByTestId("skill-tile");
    expect(tiles).toHaveLength(2);
    expect(tiles[0]).toHaveTextContent("Smithing");
    expect(tiles[0]).toHaveTextContent("Developer · 250,000 XP to level 85");
    expect(within(tiles[0]).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "29");
    expect(tiles[1]).toHaveTextContent("The instance · 13,500,000 XP");
    expect(within(tiles[1]).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
  });

  it("draws seven tier pips, the progress to the next tier, and hides a secret", () => {
    mockUseProgression.mockReturnValue({ data: progression() });
    renderPage();
    const [forged, cape, secret] = screen.getAllByTestId("achievement");
    const pips = within(forged).getAllByTestId("tier-pip");
    expect(pips).toHaveLength(7);
    expect(pips.filter((p) => p.dataset.reached === "true")).toHaveLength(3);
    expect(within(forged).getByTestId("achievement-progress")).toHaveTextContent("16 of 30 for Mithril");
    expect(cape).toHaveAttribute("data-state", "discovered");
    expect(within(cape).getByTestId("achievement-progress")).toHaveTextContent("0 of 1 for Bronze");
    expect(secret).toHaveTextContent("???");
    expect(secret).toHaveTextContent("A secret achievement.");
    expect(within(secret).queryByTestId("achievement-progress")).toBeNull();
  });

  it("offers a retry when progression does not load", () => {
    mockUseProgression.mockReturnValue({ isError: true, refetch: vi.fn() });
    renderPage();
    expect(screen.getByText("Progression did not load")).toBeInTheDocument();
  });
});
