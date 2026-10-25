import { useEffect, useMemo, useRef, useState } from "react";
import type { Loot, WorldCharacter } from "@/api/world";
import { AgentAvatar } from "@/components/agents/AgentAvatar";
import { useIdentityLookup } from "@/hooks/useAgentIdentity";
import { STATE_POSE } from "@/lib/world";
import { SPACING, STATION_OF, chestOf, hallLayout } from "@/lib/worldHall";
import { ArtObjectAt, EffectAt } from "./art/Pieces";
import { anchored } from "./art/style";
import { TerrainCanvas } from "./art/TerrainCanvas";
import type { ArtManifest } from "./art/manifest";

interface HallSceneProps {
  art: ArtManifest;
  characters: readonly WorldCharacter[];
  loot: readonly Loot[];
  xpDrops: Map<string, number>;
  levelUps: ReadonlySet<string>;
  onSelect: (agentType: string) => void;
  onBoard: () => void;
  onLoot: (jobId: string) => void;
}

/**
 * The guild hall's inside: the floor and walls, each character standing at
 * its station (the station stands empty while it is out), the quest board,
 * the loot chest. Drawn at the art's size and scaled by a whole number to
 * fill the width; a click on a character opens its panel.
 */
export function HallScene({ art, characters, loot, xpDrops, levelUps, onSelect, onBoard, onLoot }: HallSceneProps) {
  const lookup = useIdentityLookup();
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(720);
  useEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => setWidth(entries[0].contentRect.width));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  const ordered = useMemo(() => [...characters].sort((a, b) => a.agent_type.localeCompare(b.agent_type)), [characters]);
  // Twice the art's size once seven stations fit at it; the room is the width at that scale.
  const scale = width >= 2 * 8 * SPACING ? 2 : 1;
  const room = Math.floor(width / scale);
  const layout = useMemo(() => hallLayout(ordered.length, room, art.tileSize), [ordered.length, room, art.tileSize]);
  const layers = useMemo(() => [{ terrain: "wall", mask: layout.mask }], [layout.mask]);
  const chest = chestOf(loot);
  const chestAt = { x: layout.width - 44, y: layout.height - 36 };
  const torchY = 2 * art.tileSize + 2;

  return (
    <div ref={box} className="w-full overflow-x-auto" data-testid="hall-scene">
      <div style={{ width: layout.width * scale, height: layout.height * scale, position: "relative" }}>
        <div style={{ position: "absolute", inset: 0, width: layout.width, height: layout.height, transform: `scale(${scale})`, transformOrigin: "0 0" }}>
          <TerrainCanvas art={art} cols={layout.cols} rows={layout.rows} base="floor" layers={layers} />
          <ArtObjectAt art={art} name="torch" x={48} y={torchY} />
          <ArtObjectAt art={art} name="torch" x={layout.width - 48} y={torchY} />
          <ArtObjectAt art={art} name="bookshelf" x={104} y={torchY + 12} />
          <ArtObjectAt art={art} name="bookshelf" x={152} y={torchY + 12} />
          <button
            type="button"
            title="The quest board"
            aria-label="The quest board"
            onClick={onBoard}
            style={{ ...anchored(layout.width / 2 + 40, torchY + 12, { x: 24, y: 43 }), width: 48, height: 48 }}
          >
            <ArtObjectAt art={art} name="quest_board" x={24} y={43} />
          </button>
          <button
            type="button"
            title={loot.length ? `Loot: ${loot[0].title}` : "The loot chest is empty"}
            aria-label="The loot chest"
            disabled={!loot.length}
            onClick={() => loot.length && onLoot(loot[0].job_id)}
            style={{ ...anchored(chestAt.x, chestAt.y, { x: 16, y: 28 }), width: 32, height: 32 }}
            data-testid="loot-chest"
            data-chest={chest}
          >
            <ArtObjectAt art={art} name={chest} x={16} y={28} />
          </button>
          {chest === "chest_gold" && <EffectAt art={art} name="loot_sparkle" x={chestAt.x} y={chestAt.y} />}

          {ordered.map((c, i) => {
            const spot = layout.station(i);
            const station = STATION_OF[c.agent_type] ?? "desk";
            const home = c.state === "idle" || c.state === "returning" || c.state === "defeated" || c.state === "recalled";
            const identity = lookup(c.agent_type);
            const drop = xpDrops.get(c.agent_type);
            return (
              <div key={c.agent_type}>
                <ArtObjectAt art={art} name={station} x={spot.x - 10} y={spot.y} />
                <button
                  type="button"
                  onClick={() => onSelect(c.agent_type)}
                  title={`${identity.displayName}${home ? "" : " (out)"}`}
                  aria-label={identity.displayName}
                  data-testid="hall-character"
                  data-home={home}
                  style={{ ...anchored(spot.x + 22, spot.y + 2, { x: 16, y: 32 }), width: 32, height: 40 }}
                >
                  {home ? (
                    <AgentAvatar identity={identity} size={32} state={STATE_POSE[c.state]} />
                  ) : (
                    <span className="block h-8 w-8 rounded-sm border border-dashed border-white/25" />
                  )}
                  {c.state === "waiting" && <span className="world-bubble absolute -top-3 left-1/2 -translate-x-1/2">?</span>}
                  <span
                    className="sprite-nameplate absolute left-1/2 top-[31px] max-w-[66px] -translate-x-1/2 truncate whitespace-nowrap rounded-[2px] bg-black/60 px-0.5"
                    style={{ color: home ? identity.hex : "var(--color-fg-muted)", fontSize: 8, lineHeight: "10px" }}
                  >
                    {identity.displayName}
                  </span>
                </button>
                {drop ? <EffectAt art={art} name="xp_drop" x={spot.x + 22} y={spot.y - 26} label={`+${drop.toLocaleString("en-US")}`} /> : null}
                {levelUps.has(c.agent_type) && <EffectAt art={art} name="level_up" x={spot.x + 22} y={spot.y + 4} />}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
