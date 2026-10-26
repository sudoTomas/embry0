import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render as renderRaw, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";
import type { Dungeon, Lead, Region } from "@/api/world";
import { identityOf } from "@/lib/agentIdentity";
import { EMPTY_ART } from "../art/manifest";

vi.mock("@/hooks/useAgentIdentity", () => ({
  useIdentityLookup: () => (type: string) => identityOf(type),
}));

import { WorldMap } from "../WorldMap";

function render(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderRaw(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const REGION: Region = {
  repo: "acme/web",
  jobs: 3,
  last_job_at: null,
  owned: true,
  watched: true,
  areas: [{ name: "Billing", slug: "billing" }],
};

const SLOP_LEAD = {
  id: "case:9",
  kind: "slop",
  repo: "acme/web",
  area: "Billing",
  owners: [],
  title: "Clean up the outbox retry wrappers",
  summary: "",
  details: "",
  action: "",
  url: null,
  created_at: "2026-10-25T00:00:00Z",
  level: 30,
  verb: "Clean up",
  creature: "slop_ooze",
  leader: "slopmaster",
  nature: "slop",
  slop_score: 70,
} as Lead;

describe("WorldMap Slop Dungeons", () => {
  it("draws a dungeon with its score, and entering it opens its worst slop", () => {
    const onPickLead = vi.fn();
    const dungeons: Dungeon[] = [{ repo: "acme/web", area: "Billing", score: 70, open: 1, cleaned: 2 }];
    render(
      <WorldMap
        art={EMPTY_ART}
        regions={[REGION]}
        quests={[SLOP_LEAD]}
        parties={[]}
        characters={[]}
        dungeons={dungeons}
        onPickLead={onPickLead}
        onPickCharacter={() => {}}
      />,
    );
    const dungeon = screen.getByTestId("map-dungeon");
    expect(dungeon).toHaveAttribute("data-score", "70");
    expect(dungeon).toHaveAccessibleName("Slop Dungeon · Billing · slop score 70 · 1 open, 2 cleaned lately");
    fireEvent.click(dungeon);
    expect(onPickLead).toHaveBeenCalledWith(SLOP_LEAD);
  });

  it("draws no dungeon for a repository the map does not show", () => {
    render(
      <WorldMap
        art={EMPTY_ART}
        regions={[REGION]}
        quests={[]}
        parties={[]}
        characters={[]}
        dungeons={[{ repo: "acme/other", area: null, score: 40, open: 2, cleaned: 0 }]}
        onPickLead={() => {}}
        onPickCharacter={() => {}}
      />,
    );
    expect(screen.queryByTestId("map-dungeon")).toBeNull();
  });
});

describe("WorldMap Slop-O-Meter", () => {
  it("names a dungeon's Slop-O-Meter level beside its open slop", () => {
    const meter = { score: 0.73, level: 4, name: "Sloppy Joe", provisional: true, computed_at: "2026-10-26T06:00:00Z" };
    render(
      <WorldMap
        art={EMPTY_ART}
        regions={[REGION]}
        quests={[]}
        parties={[]}
        characters={[]}
        dungeons={[{ repo: "acme/web", area: "Billing", score: 0, open: 0, cleaned: 0, meter }]}
        onPickLead={() => {}}
        onPickCharacter={() => {}}
      />,
    );
    const dungeon = screen.getByTestId("map-dungeon");
    expect(dungeon).toHaveAttribute("data-meter-level", "4");
    expect(dungeon).toHaveAccessibleName(
      "Slop Dungeon · Billing · slop score 0 · 0 open, 0 cleaned lately · Slop-O-Meter Sloppy Joe (0.73, provisional)",
    );
    expect(dungeon).toHaveTextContent("Slop 0 · Sloppy Joe");
  });
});
