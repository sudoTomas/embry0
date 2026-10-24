import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { progression } from "@/components/progression/__tests__/fixtures";

const mockUseProgression = vi.fn();
vi.mock("@/hooks/useProgression", () => ({ useProgression: () => mockUseProgression() }));
vi.mock("@/hooks/useAgentIdentity", () => ({
  useIdentityLookup: () => (type: string) => ({ displayName: type === "developer" ? "Developer" : "Review" }),
}));
vi.mock("@/components/agents/AgentAvatar", () => ({ AgentAvatar: () => <span data-testid="avatar" /> }));

import { AchievementsPage } from "../AchievementsPage";

function renderPage(path = "/achievements") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AchievementsPage />
    </MemoryRouter>,
  );
}

describe("AchievementsPage", () => {
  it("leads with the account, then lists its characters by total level", () => {
    mockUseProgression.mockReturnValue({ data: progression() });
    renderPage();
    expect(screen.getByTestId("account-summary")).toHaveTextContent(
      "Account 15,134,431 XP · account total 192 · 2 characters · 1 of 3 achievements unlocked",
    );
    const characters = screen.getAllByTestId("character");
    expect(characters.map((c) => c.textContent)).toEqual(["DeveloperCoding 99187", "ReviewCoding 15"]);
  });

  it("shows the highest character's skill sheet, grouped, until another is picked", async () => {
    mockUseProgression.mockReturnValue({ data: progression() });
    renderPage();
    const sheet = screen.getByTestId("skill-sheet");
    expect(sheet).toHaveAccessibleName("Developer's skills");
    expect(within(sheet).getByTestId("worn")).toHaveTextContent("Divine gear · Coding cape");
    expect(within(sheet).getAllByRole("heading", { level: 3 }).map((h) => h.textContent)).toEqual([
      "Build",
      "Verify",
      "Investigate",
      "Care",
    ]);
    const tiles = within(sheet).getAllByTestId("skill-tile");
    expect(tiles).toHaveLength(5);
    expect(tiles[0]).toHaveTextContent("Coding99");
    expect(within(tiles[0]).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
    expect(tiles[1]).toHaveTextContent("250,000 XP to level 85");
    expect(within(tiles[1]).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "29");

    await userEvent.click(screen.getAllByTestId("character")[1]);
    expect(screen.getByTestId("skill-sheet")).toHaveAccessibleName("Review's skills");
    expect(screen.getAllByTestId("character")[1]).toHaveAttribute("aria-current", "true");
  });

  it("opens the character the link names", () => {
    mockUseProgression.mockReturnValue({ data: progression() });
    renderPage("/achievements?character=review");
    expect(screen.getByTestId("skill-sheet")).toHaveAccessibleName("Review's skills");
    expect(screen.getByTestId("worn")).toHaveTextContent(/^Bronze gear$/);
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
