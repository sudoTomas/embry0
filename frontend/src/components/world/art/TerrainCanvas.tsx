import { useEffect, useRef } from "react";
import { artUrl, type ArtManifest, type Tileset } from "./manifest";

/** One terrain layer: a corner mask and the tileset terrain it stands for. */
export interface TerrainLayer {
  terrain: string;
  mask: Uint8Array;
}

interface TerrainCanvasProps {
  art: ArtManifest;
  cols: number;
  rows: number;
  /** The terrain under everything (every tileset of the overworld shares its grass). */
  base: string;
  /** Drawn in order over the base. */
  layers: readonly TerrainLayer[];
  scale?: number;
  className?: string;
}

const IMAGES = new Map<string, Promise<HTMLImageElement>>();

function loadImage(src: string): Promise<HTMLImageElement> {
  let image = IMAGES.get(src);
  if (!image) {
    image = new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = reject;
      img.src = src;
    });
    IMAGES.set(src, image);
  }
  return image;
}

/** The tileset that draws ``terrain`` and whether it is that tileset's corner value 1. */
function tilesetFor(art: ArtManifest, terrain: string): [Tileset, 0 | 1] | undefined {
  for (const tileset of Object.values(art.tilesets)) {
    if (!tileset) continue;
    if (tileset.terrains["1"] === terrain) return [tileset, 1];
    if (tileset.terrains["0"] === terrain) return [tileset, 0];
  }
  return undefined;
}

/**
 * The terrain of a map, painted once into a canvas at the art's own size and
 * shown at an integer ``scale`` with nearest-neighbour scaling. A tile's
 * index comes from its four corners (8·NW + 4·NE + 2·SW + 1·SE, a corner
 * being 1 where the tileset's terrain "1" is); tile ``i`` sits at column
 * i % 4, row i ÷ 4 of the tileset's sheet.
 */
export function TerrainCanvas({ art, cols, rows, base, layers, scale = 1, className }: TerrainCanvasProps) {
  const ref = useRef<HTMLCanvasElement>(null);
  const tile = art.tileSize;

  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext?.("2d");
    if (!canvas || !ctx) return;
    let cancelled = false;
    const baseSet = tilesetFor(art, base);
    const plans = layers.map((layer) => ({ layer, set: tilesetFor(art, layer.terrain) }));
    const needed = [baseSet, ...plans.map((p) => p.set)].filter((s): s is [Tileset, 0 | 1] => !!s).map(([t]) => t.image);
    void Promise.all([...new Set(needed)].map((image) => loadImage(artUrl(image)).then((img) => [image, img] as const)))
      .then((loaded) => {
        if (cancelled) return;
        const images = new Map(loaded);
        ctx.imageSmoothingEnabled = false;
        const draw = (image: string, index: number, c: number, r: number) => {
          const img = images.get(image);
          if (img) ctx.drawImage(img, (index % 4) * tile, Math.floor(index / 4) * tile, tile, tile, c * tile, r * tile, tile, tile);
        };
        if (baseSet) {
          const [set, value] = baseSet;
          const full = value === 1 ? 15 : 0;
          for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) draw(set.image, full, c, r);
        } else {
          ctx.fillStyle = "#4a7a2e";
          ctx.fillRect(0, 0, cols * tile, rows * tile);
        }
        const w = cols + 1;
        for (const { layer, set } of plans) {
          if (!set) continue;
          const [tileset, value] = set;
          const m = layer.mask;
          for (let r = 0; r < rows; r++) {
            for (let c = 0; c < cols; c++) {
              // The corner is the terrain where the mask says so; flip for a tileset where it is value 0.
              const at = (cc: number, rr: number) => (value === 1 ? m[rr * w + cc] : 1 - m[rr * w + cc]);
              const index = 8 * at(c, r) + 4 * at(c + 1, r) + 2 * at(c, r + 1) + at(c + 1, r + 1);
              const none = value === 1 ? index === 0 : index === 15;
              if (!none) draw(tileset.image, index, c, r);
            }
          }
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [art, cols, rows, base, layers, tile]);

  return (
    <canvas
      ref={ref}
      width={cols * tile}
      height={rows * tile}
      className={className}
      aria-hidden="true"
      style={{ width: cols * tile * scale, height: rows * tile * scale, imageRendering: "pixelated", display: "block" }}
    />
  );
}
