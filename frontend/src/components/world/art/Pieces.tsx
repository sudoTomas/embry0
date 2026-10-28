import type { ReactNode } from "react";
import { ArtSprite } from "./ArtSprite";
import type { ArtManifest } from "./manifest";
import { anchored } from "./style";

/** An object of the objects atlas standing on (x, y); nothing when the manifest does not name it. */
export function ArtObjectAt({ art, name, x, y, title, children }: { art: ArtManifest; name: string; x: number; y: number; title?: string; children?: ReactNode }) {
  const obj = art.objects[name];
  const atlas = obj ? art.atlases[obj.atlas] : undefined;
  if (!obj || !atlas) return null;
  return (
    <ArtSprite image={atlas.image} rect={obj.rect} style={anchored(x, y, obj.anchor)} title={title}>
      {children}
    </ArtSprite>
  );
}

/** A creature of the creatures atlas on (x, y), its idle loop stepping. */
export function CreatureAt({ art, name, x, y, title, faded }: { art: ArtManifest; name: string; x: number; y: number; title?: string; faded?: boolean }) {
  const creature = art.creatures[name];
  const atlas = creature ? art.atlases[creature.atlas] : undefined;
  if (!creature || !atlas) return null;
  return (
    <ArtSprite
      image={atlas.image}
      rect={creature.frames[0]}
      frames={creature.frames}
      animation={`creature-${name}`}
      fps={creature.fps}
      loop={creature.loop}
      style={{ ...anchored(x, y, creature.anchor), opacity: faded ? 0.45 : 1 }}
      title={title}
    />
  );
}

/**
 * An effect on (x, y): xp_drop rising, level_up fireworks, loot_sparkle
 * looping, hit_marker as a badge with ``label`` written on it (its one frame
 * made to flash by ``className``). A one-shot effect calls ``onEnd`` when it
 * is over.
 */
export function EffectAt({
  art,
  name,
  x,
  y,
  label,
  onEnd,
  scale = 1,
  className,
}: {
  art: ArtManifest;
  name: string;
  x: number;
  y: number;
  label?: string;
  onEnd?: () => void;
  scale?: number;
  className?: string;
}) {
  const effect = art.vfx[name];
  if (!effect) return null;
  return (
    <ArtSprite
      image={effect.image}
      rect={effect.frames[0]}
      frames={effect.frames}
      animation={`vfx-${name}`}
      fps={effect.fps}
      loop={effect.loop}
      scale={scale}
      className={className}
      onAnimationEnd={onEnd}
      lit
      style={{ ...anchored(x, y, { x: effect.anchor.x * scale, y: effect.anchor.y * scale }), zIndex: 100_000, pointerEvents: "none" }}
    >
      {label !== undefined && (
        <span
          className="hud absolute inset-0 flex items-center justify-center text-[11px] font-bold text-white"
          style={{ textShadow: "1px 1px 0 #000" }}
        >
          {label}
        </span>
      )}
    </ArtSprite>
  );
}
