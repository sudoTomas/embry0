import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render as renderRaw, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";
import type { Loot, Party, Region, WorldCharacter } from "@/api/world";
import { identityOf } from "@/lib/agentIdentity";
import { EMPTY_ART, type ArtManifest } from "../art/manifest";
import { NightfallFilter } from "../art/Nightfall";
import { ArtObjectAt, EffectAt } from "../art/Pieces";
import { NIGHT_MATRIX, NIGHTFALL_FILTER_ID, nightMatrixValues, relightPixels } from "../art/relight";

vi.mock("@/hooks/useAgentIdentity", () => ({
  useIdentityLookup: () => (type: string) => identityOf(type),
}));

import { HallScene } from "../HallScene";
import { WorldMap } from "../WorldMap";

function render(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderRaw(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function relit(r: number, g: number, b: number, a = 255): number[] {
  const px = new Uint8ClampedArray([r, g, b, a]);
  relightPixels(px);
  return [...px];
}

describe("Nightfall: the re-light (spec 2026-10-27)", () => {
  it("takes a colour toward the black, keeps its alpha, and leans the shadows blue", () => {
    const [r, , b, a] = relit(255, 255, 255, 200);
    expect(a).toBe(200);
    expect(r).toBeLessThan(150);
    expect(b).toBeGreaterThan(r);
    expect(relit(0, 0, 0)).toEqual([0, 0, 0 + Math.round(255 * 0.03), 255]);
  });

  it("dims a colour and drains most of its saturation without changing its hue", () => {
    const [r, g, b] = relit(212, 175, 55);
    expect(r).toBeLessThan(212 * 0.6);
    expect(r).toBeGreaterThan(g);
    expect(g).toBeGreaterThan(b);
    const spread = (x: number[]) => Math.max(...x) - Math.min(...x);
    expect(spread([r, g, b])).toBeLessThan(spread([212, 175, 55]) * 0.45);
  });

  it("draws one filter, the same matrix the canvas pass uses", () => {
    expect(NIGHT_MATRIX).toHaveLength(4);
    expect(NIGHT_MATRIX[3]).toEqual([0, 0, 0, 1, 0]);
    const { container } = render(<NightfallFilter />);
    const filter = container.querySelector(`filter#${NIGHTFALL_FILTER_ID}`);
    expect(filter).toHaveAttribute("color-interpolation-filters", "sRGB");
    const values = filter?.querySelector("feColorMatrix")?.getAttribute("values") ?? "";
    expect(values).toBe(nightMatrixValues());
    expect(values.split(" ")).toHaveLength(20);
  });
});

const ART: ArtManifest = {
  ...EMPTY_ART,
  atlases: { objects: { image: "objects.png", width: 64, height: 64 }, fx: { image: "vfx/level_up.png", width: 32, height: 32 } },
  objects: {
    torch: { atlas: "objects", rect: [0, 0, 32, 32], anchor: { x: 15, y: 29 }, bounds: [0, 0, 32, 32], category: "hall" },
  },
  vfx: { level_up: { image: "vfx/level_up.png", frames: [[0, 0, 32, 32]], fps: 6, loop: false, anchor: { x: 16, y: 16 } } },
};

describe("Nightfall: what is re-lit", () => {
  it("re-lights a sheet but leaves an effect as drawn, light being light", () => {
    const { container } = render(
      <div>
        <ArtObjectAt art={ART} name="torch" x={0} y={0} />
        <EffectAt art={ART} name="level_up" x={0} y={0} />
      </div>,
    );
    const sheets = [...container.querySelectorAll(".art-sheet")];
    expect(sheets).toHaveLength(2);
    expect(sheets.filter((s) => s.classList.contains("art-lit"))).toHaveLength(1);
    expect(sheets[1]).toHaveClass("art-lit");
  });
});

const CHARACTER = { agent_type: "developer", state: "idle", level: 3, job_id: null } as unknown as WorldCharacter;
const LOOT: Loot = { job_id: "job-1", repo: "acme/web", area: null, title: "Add the export", pr_url: "https://example.com/pull/7", merged: false, at: null };

describe("Nightfall: where the light falls", () => {
  const hall = (loot: Loot[]) =>
    render(
      <HallScene art={ART} characters={[CHARACTER]} loot={loot} xpDrops={new Map()} levelUps={new Set()} onSelect={() => {}} onBoard={() => {}} onLoot={() => {}} />,
    );

  it("lights the hall at its two torches and its quest board, and loot waiting for a look", () => {
    const { unmount } = hall([]);
    expect(screen.getByTestId("hall-scene")).toHaveClass("nightfall");
    expect(screen.getAllByTestId("torchlight")).toHaveLength(3);
    unmount();
    hall([LOOT]);
    expect(screen.getAllByTestId("torchlight")).toHaveLength(4);
  });

  const REGION: Region = { repo: "acme/web", jobs: 1, last_job_at: null, owned: true, watched: true, areas: [] };
  const party = (state: Party["state"]) => ({ job_id: `job-${state}`, repo: "acme/web", area: null, title: "A quest", state, leader: "developer" }) as unknown as Party;

  it("lights the map at the hall's door and carries a light with every party out, but a beaten one's goes out", () => {
    render(
      <WorldMap
        art={EMPTY_ART}
        regions={[REGION]}
        quests={[]}
        parties={[party("working"), party("defeated")]}
        characters={[]}
        onPickLead={() => {}}
        onPickCharacter={() => {}}
      />,
    );
    expect(screen.getAllByTestId("map-party")).toHaveLength(2);
    expect(screen.getAllByTestId("torchlight")).toHaveLength(2);
    expect(document.querySelector(".nightfall.night-frame")).not.toBeNull();
  });
});
