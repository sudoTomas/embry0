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
  it("leads with the account, then lists its characters by level", () => {
    mockUseProgression.mockReturnValue({ data: progression() });
    renderPage();
    expect(screen.getByTestId("account-summary")).toHaveTextContent(
      "Account 15,134,431 XP · account total 192 · 2 characters · 2 of 4 achievements unlocked · 2 quest points",
    );
    const characters = screen.getAllByTestId("character");
    expect(characters.map((c) => c.textContent)).toEqual(["DeveloperCoding 9959", "ReviewCoding 11"]);
  });

  it("shows the highest character's skill sheet, grouped, until another is picked", async () => {
    mockUseProgression.mockReturnValue({ data: progression() });
    renderPage();
    const sheet = screen.getByTestId("skill-sheet");
    expect(sheet).toHaveAccessibleName("Developer's skills");
    expect(within(sheet).getByTestId("worn")).toHaveTextContent("Divine gear · Coding cape");
    expect(within(sheet).getByTestId("sheet-levels")).toHaveTextContent("· level 59 · total level 187");
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

  it("speaks the content pack's words, with no copy of its own (#176)", () => {
    const base = progression();
    const tiers = base.tiers.map((t) => (t.id === "divine" ? { ...t, name: "Radiant" } : t));
    const groups = base.groups.map((g) => (g.id === "build" ? { ...g, name: "Craft" } : g));
    const labels = { ...base.labels, gear: "{tier} kit", mastery_cloak: "{skill} mantle" };
    mockUseProgression.mockReturnValue({ data: { ...base, tiers, groups, labels } });
    renderPage();
    const sheet = screen.getByTestId("skill-sheet");
    expect(within(sheet).getByTestId("worn")).toHaveTextContent("Radiant kit · Coding mantle");
    expect(within(sheet).getAllByRole("heading", { level: 3 })[0]).toHaveTextContent("Craft");
    expect(screen.getByTestId("tier-legend")).toHaveTextContent("Radiant");
  });

  it("names the pack's calendar once, beside the secrets the oddities hide (#176)", () => {
    const base = progression();
    mockUseProgression.mockReturnValue({ data: { ...base, pack: { ...base.pack, timezone: "Europe/Berlin" } } });
    renderPage();
    const named = screen.queryAllByTestId("pack-timezone");
    expect(named, "the timezone is named once on the page").toHaveLength(1);
    expect(named[0]).toHaveTextContent("on the Europe/Berlin calendar");
  });

  it("says when the achievements were counted on UTC because the database does not know the pack's timezone", () => {
    const base = progression();
    mockUseProgression.mockReturnValue({
      data: { ...base, pack: { ...base.pack, timezone: "Europe/Berlin", timezone_applied: "UTC" } },
    });
    renderPage();
    const note = screen.queryByTestId("pack-timezone")?.textContent ?? "";
    expect(note, "the fallback calendar is named, not the pack's").toContain("on the UTC calendar for now");
    expect(note).toContain("does not know the pack's timezone, Europe/Berlin");
  });

  it("opens the character the link names", () => {
    mockUseProgression.mockReturnValue({ data: progression() });
    renderPage("/achievements?character=review");
    expect(screen.getByTestId("skill-sheet")).toHaveAccessibleName("Review's skills");
    expect(screen.getByTestId("worn")).toHaveTextContent(/^Bronze gear$/);
  });

  it("leads with the diary: totals, the named tier ladder, and recent unlocks", () => {
    mockUseProgression.mockReturnValue({
      data: progression({
        recent: [
          { kind: "achievement", at: "2026-10-05T00:00:00+00:00", id: "forged_in_fire", name: "Shipped It", tier: "gold" },
          { kind: "achievement", at: "2026-10-04T00:00:00+00:00", id: "forged_in_fire", name: "Shipped It", tier: "silver" },
          { kind: "achievement", at: "2026-10-03T00:00:00+00:00", id: "long_grind", name: "Long Grind", tier: "complete" },
        ],
      }),
    });
    renderPage();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("embry0 Achievements");
    const tiles = screen.getAllByTestId("diary-tile").map((t) => t.textContent);
    expect(tiles[0]).toContain("2 / 4");
    expect(tiles[1]).toContain("1");
    expect(tiles[3]).toContain("Gold");
    expect(tiles[4]).toContain("Shipped It");
    expect(within(screen.getByTestId("tier-legend")).getAllByRole("listitem").map((li) => li.textContent?.replace("→", "").trim())).toEqual([
      "Bronze", "Silver", "Gold", "Platinum", "Diamond", "Obsidian", "Divine",
    ]);
    const recent = within(screen.getByTestId("recent-unlocks")).getAllByTitle(/·/);
    expect(recent.map((r) => r.textContent)).toEqual(["Shipped It", "Long Grind"]);
  });

  it("names each card's state and tier in words, with evidence, what counts, and progress to the next tier", async () => {
    mockUseProgression.mockReturnValue({ data: progression() });
    renderPage();
    const [forged, cape, grind, secret] = screen.getAllByTestId("achievement");
    expect(within(forged).getByTestId("state-chip")).toHaveTextContent("unlocked");
    expect(within(forged).getByTestId("tier-chip")).toHaveTextContent("Gold");
    expect(forged).toHaveTextContent("Somebody ring the bell.");
    expect(within(forged).getByRole("link", { name: "o/app #42" })).toHaveAttribute("href", "https://forge/o/app/pull/42");
    expect(within(forged).getByTestId("achievement-progress")).toHaveTextContent("16 / 30");
    expect(within(forged).getByRole("progressbar")).toHaveAttribute("aria-label", "Progress to Platinum");
    expect(within(forged).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "7");
    await userEvent.click(within(forged).getByText("What counts"));
    expect(within(forged).getByTestId("what-counts")).toHaveTextContent("Bronze 1 · Silver 5 · Gold 15 · Platinum 30");
    expect(within(cape).getByTestId("tier-chip")).toHaveTextContent("target Bronze");
    expect(within(grind).getByTestId("tier-chip")).toHaveTextContent("Complete");
    expect(within(grind).getByTestId("achievement-progress")).toHaveTextContent(/^done$/);
    expect(secret).toHaveTextContent("???");
    expect(within(secret).queryByTestId("what-counts")).toBeNull();
    expect(within(secret).queryByTestId("achievement-progress")).toBeNull();
  });

  it("filters by category and by state, from the address", async () => {
    mockUseProgression.mockReturnValue({ data: progression() });
    renderPage("/achievements?state=unlocked");
    expect(screen.getAllByTestId("achievement").map((a) => a.dataset.category)).toEqual(["shipping", "oddities"]);
    await userEvent.click(screen.getByRole("button", { name: "Oddities" }));
    expect(screen.getAllByTestId("achievement").map((a) => a.dataset.category)).toEqual(["oddities"]);
    await userEvent.click(screen.getByRole("button", { name: "secret" }));
    expect(screen.getByText("No achievement matches these filters.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "All" }));
    expect(screen.getAllByTestId("achievement").map((a) => a.dataset.state)).toEqual(["secret"]);
  });

  it("offers a retry when progression does not load", () => {
    mockUseProgression.mockReturnValue({ isError: true, refetch: vi.fn() });
    renderPage();
    expect(screen.getByText("Progression did not load")).toBeInTheDocument();
  });
});
