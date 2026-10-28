import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Progression } from "@/api/progression";
import { label, gamePackOf, tierName } from "@/lib/gamePack";
import { character, progression } from "./fixtures";
import { outfitOf } from "../outfits";
import { SEEN_KEY } from "../chatbox";

/**
 * An answer from a backend older than the content pack (#176): an orchestrator
 * deployed or rolled back alone, or the gap between two container restarts. It
 * has no pack fields, and the pages must still read sensibly.
 */
function older(): Progression {
  const { pack, groups, tiers, rares, cloaks, diary_tiers, messages, labels, curve, ...rest } = progression();
  void [pack, groups, tiers, rares, cloaks, diary_tiers, messages, labels, curve];
  const characters = rest.characters.map(({ gear, ...c }) => (void gear, c));
  const { cloak_trim, ...account } = rest.account;
  void cloak_trim;
  return { ...rest, characters, account } as unknown as Progression;
}

const mockUseProgression = vi.fn();
vi.mock("@/hooks/useProgression", () => ({ useProgression: () => mockUseProgression() }));
vi.mock("@/hooks/useAgentIdentity", () => ({
  useIdentityLookup: () => (type: string) => ({ type, displayName: type, avatar: "golem", color: "slate" }),
}));
vi.mock("@/components/agents/AgentAvatar", () => ({ AgentAvatar: () => <span data-testid="avatar" /> }));

import { AchievementsPage } from "@/pages/AchievementsPage";
import { Chatbox } from "../Chatbox";

afterEach(() => window.localStorage.clear());

describe("against a backend older than the content pack", () => {
  it("names tiers, labels and groups readably instead of printing raw ids", () => {
    const pack = gamePackOf(older());
    expect(tierName(pack, "obsidian"), "a tier id reads as a word").toBe("Obsidian");
    expect(label(pack, "quest_journal"), "a label key reads as words").toBe("Quest journal");
    expect(label(pack, "gear", { tier: "Gold" }), "a label reads its values, then its key").toBe("Gold gear");
    expect(pack.groups.map((g) => g.name), "the groups the skills name").toEqual(["Build", "Verify", "Investigate", "Care"]);
  });

  it("still shows the skill sheet's tiles, grouped by the groups the skills name", () => {
    mockUseProgression.mockReturnValue({ data: older() });
    render(
      <MemoryRouter initialEntries={["/achievements"]}>
        <AchievementsPage />
      </MemoryRouter>,
    );
    const sheet = screen.getByTestId("skill-sheet");
    expect(within(sheet).queryAllByTestId("skill-tile"), "every skill keeps its tile").toHaveLength(5);
    expect(within(sheet).getByTestId("worn"), "the gear reads in words").toHaveTextContent("Divine gear");
  });

  it("dresses a character in its tier's gear, not bronze", () => {
    const { gear, ...c } = character({ tier: "obsidian" });
    void gear;
    expect(outfitOf(c, gamePackOf(older())).tier, "the tier id is the gear step").toBe("obsidian");
  });

  it("shows no empty chat bubble and marks nothing seen", () => {
    window.localStorage.setItem(SEEN_KEY, "2026-10-01T00:00:00+00:00");
    const data = { ...older(), recent: [{ kind: "agent_level", at: "2026-10-24T10:00:00+00:00", agent_type: "developer", level: 9 }] };
    mockUseProgression.mockReturnValue({ data });
    render(<Chatbox />);
    expect(screen.queryByTestId("chatbox-message"), "no empty bubble").toBeNull();
    expect(window.localStorage.getItem(SEEN_KEY), "the message waits for an answer that can say it").toBe("2026-10-01T00:00:00+00:00");
  });
});
