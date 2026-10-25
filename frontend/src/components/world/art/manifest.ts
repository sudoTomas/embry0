import { useQuery } from "@tanstack/react-query";

/**
 * The world's art (public/world/README.md is the schema): corner tilesets,
 * atlases, objects, creatures and effects. Every `image` is relative to
 * `/world/`. A missing or broken manifest is the empty one, and the world is
 * drawn with placeholders.
 */
export type Rect = [number, number, number, number];
export interface Anchor {
  x: number;
  y: number;
}

export interface Tileset {
  image: string;
  layout: "corner-4x4";
  /** The terrain a corner of value 0 and of value 1 stands for. */
  terrains: { "0": string; "1": string };
}

export interface Atlas {
  image: string;
  width: number;
  height: number;
}

export interface ArtObject {
  atlas: string;
  rect: Rect;
  anchor: Anchor;
  bounds: [number, number, number, number];
  category: "scenery" | "town" | "building" | "station" | "hall" | "loot";
}

export interface Creature {
  atlas: string;
  frames: Rect[];
  fps: number;
  loop: boolean;
  size: number;
  anchor: Anchor;
  bounds: [number, number, number, number];
  /** The lead kinds it stands for. */
  leadKinds: string[];
  interaction: "attack" | "talk" | "start_quest" | "repair";
}

export interface Effect {
  image: string;
  frames: Rect[];
  fps: number;
  loop: boolean;
  anchor: Anchor;
}

export interface ArtManifest {
  version: 1;
  tileSize: number;
  tilesets: Partial<Record<string, Tileset>>;
  atlases: Partial<Record<string, Atlas>>;
  objects: Partial<Record<string, ArtObject>>;
  creatures: Partial<Record<string, Creature>>;
  vfx: Partial<Record<string, Effect>>;
}

export const EMPTY_ART: ArtManifest = { version: 1, tileSize: 32, tilesets: {}, atlases: {}, objects: {}, creatures: {}, vfx: {} };

export const ART_ROOT = "/world/";
export const MANIFEST_URL = `${ART_ROOT}manifest.json`;

export function artUrl(image: string): string {
  return `${ART_ROOT}${image.replace(/^\/+/, "")}`;
}

/** Reads the manifest; anything missing or of another version reads as no art at all. */
export async function fetchArt(fetcher: typeof fetch = fetch): Promise<ArtManifest> {
  try {
    const resp = await fetcher(MANIFEST_URL, { cache: "no-cache" });
    if (!resp.ok) return EMPTY_ART;
    const data = (await resp.json()) as Partial<ArtManifest>;
    if (data.version !== 1) return EMPTY_ART;
    return {
      version: 1,
      tileSize: data.tileSize ?? 32,
      tilesets: data.tilesets ?? {},
      atlases: data.atlases ?? {},
      objects: data.objects ?? {},
      creatures: data.creatures ?? {},
      vfx: data.vfx ?? {},
    };
  } catch {
    return EMPTY_ART;
  }
}

export function useArt(): ArtManifest {
  const { data } = useQuery({ queryKey: ["world", "art"], queryFn: () => fetchArt(), staleTime: Infinity });
  return data ?? EMPTY_ART;
}

/** The creature a lead kind stands as: the one whose `leadKinds` names it; undefined for a kind no creature names. */
export function creatureFor(art: ArtManifest, kind: string): [string, Creature] | undefined {
  for (const [name, creature] of Object.entries(art.creatures)) {
    if (creature && creature.leadKinds.includes(kind)) return [name, creature];
  }
  return undefined;
}
