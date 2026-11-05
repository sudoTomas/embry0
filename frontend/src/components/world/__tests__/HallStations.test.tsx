import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { WorldCharacter } from "@/api/world";
import { identityOf } from "@/lib/agentIdentity";
import { EMPTY_ART } from "../art/manifest";

vi.mock("@/hooks/useAgentIdentity", () => ({
  useIdentityLookup: () => (type: string) => identityOf(type),
}));

import { HallScene } from "../HallScene";

const character = (agent_type: string, station?: string) =>
  ({ agent_type, state: "idle", level: 3, job_id: null, ...(station ? { station } : {}) }) as unknown as WorldCharacter;

/** Where the hall draws each character: its agent row's station (#167), whatever its name. */
describe("the guild hall's stations", () => {
  it("draws each character at the station its row names, and a desk for one that names none", () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <HallScene
          art={EMPTY_ART}
          characters={[character("scout", "lectern"), character("developer", "anvil"), character("helper"), character("odd", "throne")]}
          loot={[]}
          xpDrops={new Map()}
          levelUps={new Set()}
          onSelect={() => {}}
          onBoard={() => {}}
          onLoot={() => {}}
        />
      </QueryClientProvider>,
    );
    const stations = screen.getAllByTestId("hall-station").map((el) => el.getAttribute("data-station"));
    // Drawn in agent-type order: developer, helper, odd, scout.
    expect(stations).toEqual(["anvil", "desk", "desk", "lectern"]);
  });
});
