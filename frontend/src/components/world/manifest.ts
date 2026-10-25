import { useQuery } from "@tanstack/react-query";

/**
 * The world's art (spec 2026-10-25, Art): `public/world/manifest.json` names
 * every asset; the renderer draws a placeholder for anything it does not
 * name, so the art can land after the engine and piece by piece.
 *
 * Every `src` is relative to `/world/`. A sheet is one horizontal strip of
 * `frames` frames of `width` by `height` pixels, stepped at `fps`.
 */
export interface SheetAsset {
  src: string;
  width: number;
  height: number;
  frames?: number;
  fps?: number;
}

export interface WorldManifest {
  version: 1;
  /** The overworld's base tile, in pixels. */
  tileSize: number;
  /** One per creature a lead stands as: creature, quest-giver, signpost, golem, wyrm, imp, ghost, mystery. */
  creatures: Partial<Record<string, SheetAsset>>;
  /** Terrain tiles: grass, path, water, shore, cliff, forest, town. */
  terrain: Partial<Record<string, SheetAsset>>;
  /** guild-hall (outside), guild-hall-interior. */
  buildings: Partial<Record<string, SheetAsset>>;
  /** quest-board, loot-chest, station-anvil, station-lectern, station-alchemy, station-map, station-desk, signpost, tree, rock. */
  props: Partial<Record<string, SheetAsset>>;
  /** xp-drop, hitsplat, level-up, loot-sparkle. */
  vfx: Partial<Record<string, SheetAsset>>;
}

export const EMPTY_MANIFEST: WorldManifest = { version: 1, tileSize: 32, creatures: {}, terrain: {}, buildings: {}, props: {}, vfx: {} };

export const MANIFEST_URL = "/world/manifest.json";

/** Reads the manifest once; a missing or broken one is the empty manifest, and every asset is a placeholder. */
export async function fetchManifest(fetcher: typeof fetch = fetch): Promise<WorldManifest> {
  try {
    const resp = await fetcher(MANIFEST_URL, { cache: "no-cache" });
    if (!resp.ok) return EMPTY_MANIFEST;
    const data = (await resp.json()) as Partial<WorldManifest>;
    if (data.version !== 1) return EMPTY_MANIFEST;
    return {
      ...EMPTY_MANIFEST,
      ...data,
      creatures: data.creatures ?? {},
      terrain: data.terrain ?? {},
      buildings: data.buildings ?? {},
      props: data.props ?? {},
      vfx: data.vfx ?? {},
    } as WorldManifest;
  } catch {
    return EMPTY_MANIFEST;
  }
}

export function useWorldManifest(): WorldManifest {
  const { data } = useQuery({ queryKey: ["world", "manifest"], queryFn: () => fetchManifest(), staleTime: Infinity });
  return data ?? EMPTY_MANIFEST;
}

export function assetUrl(asset: SheetAsset): string {
  return `/world/${asset.src.replace(/^\/+/, "")}`;
}
