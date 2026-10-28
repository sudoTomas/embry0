import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import type { Dungeon, Lead, Party, Region, WorldCharacter } from "@/api/world";
import { AgentAvatar } from "@/components/agents/AgentAvatar";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { useIdentityLookup } from "@/hooks/useAgentIdentity";
import { STATE_POSE, placeOf } from "@/lib/world";
import { buildTileMap, hash, leadSpot, TERRAINS, type TileMap } from "@/lib/worldTiles";
import { ArtSprite } from "./art/ArtSprite";
import { Torchlight } from "./art/Nightfall";
import { ArtObjectAt, EffectAt } from "./art/Pieces";
import { anchored } from "./art/style";
import { TerrainCanvas } from "./art/TerrainCanvas";
import { creatureFor, type ArtManifest } from "./art/manifest";
import { CreatureIcon } from "./CreatureIcon";

interface WorldMapProps {
  art: ArtManifest;
  regions: readonly Region[];
  quests: readonly Lead[];
  parties: readonly Party[];
  characters: readonly WorldCharacter[];
  /** Slop Dungeons (spec 2026-10-25): each repository's or area's open slop and its score. */
  dungeons?: readonly Dungeon[];
  onPickLead: (lead: Lead) => void;
  onPickCharacter: (agentType: string) => void;
}

/** Where a Slop Dungeon stands: beside its area's town, else beside its region's name. */
function dungeonSpot(map: TileMap, dungeon: Dungeon): { x: number; y: number } | null {
  const region = map.regions.find((r) => r.repo === dungeon.repo);
  if (!region) return null;
  const town = dungeon.area ? region.towns.find((t) => t.name === dungeon.area) : undefined;
  return town ? { x: town.x + 26, y: town.y - 4 } : { x: region.label.x + 44, y: region.label.y + 8 };
}

/** How long a party walks between the hall and its quest. */
const WALK_MS = 4_000;

/** Where a party stands: at its quest while out, at the hall's door once back. */
function partyTarget(map: TileMap, party: Party): { x: number; y: number } {
  if (party.state === "returning" || party.state === "recalled") return { x: map.hall.x + 20, y: map.hall.y + 40 };
  if (!party.repo || !map.regions.some((r) => r.repo === party.repo)) return { x: map.hall.x + 40, y: map.hall.y + 48 };
  const spot = party.lead_id ? leadSpot(map, party.repo, party.lead_id) : map.regions.find((r) => r.repo === party.repo)!.centre;
  return { x: spot.x + 20, y: spot.y };
}

/** One party on the map: it sets out from the hall's door and walks to its quest, and back, carrying its light (a beaten party's goes out). */
function PartyOnMap({ map, art, party, onPick }: { map: TileMap; art: ArtManifest; party: Party; onPick: () => void }) {
  const identity = useIdentityLookup()(party.leader);
  const target = partyTarget(map, party);
  const [at, setAt] = useState({ x: map.hall.x, y: map.hall.y + 40 });
  const [walking, setWalking] = useState(true);
  useEffect(() => {
    const frame = requestAnimationFrame(() => setAt(target));
    setWalking(true);
    const done = window.setTimeout(() => setWalking(false), WALK_MS);
    return () => {
      cancelAnimationFrame(frame);
      window.clearTimeout(done);
    };
    // The party walks whenever its destination changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target.x, target.y]);
  const moving = walking && (party.state === "travelling" || party.state === "returning" || party.state === "working" || party.state === "waiting");
  const walk = `left ${WALK_MS}ms linear, top ${WALK_MS}ms linear`;
  return (
    <>
    <button
      type="button"
      onClick={onPick}
      title={`${identity.displayName}: ${party.title}`}
      data-testid="map-party"
      data-state={party.state}
      style={{
        ...anchored(at.x, at.y, { x: 16, y: 32 }),
        width: 32,
        height: 32,
        transition: walk,
      }}
    >
      {party.state === "waiting" && <span className="world-bubble absolute -top-4 left-1/2 -translate-x-1/2">?</span>}
      <AgentAvatar identity={identity} size={32} state={STATE_POSE[party.state]} walking={moving} />
      {party.state === "defeated" && <EffectAt art={art} name="hitsplat" x={16} y={20} label="0" className="world-hitsplat" />}
      {party.state === "returning" && party.pr_url && <EffectAt art={art} name="loot_sparkle" x={16} y={36} />}
    </button>
    {party.state !== "defeated" && <Torchlight x={at.x} y={at.y - 14} radius={56} style={{ transition: walk }} />}
    </>
  );
}

/**
 * The overworld (spec 2026-10-25, Places): a tile map of grass, dirt paths
 * from the guild hall to every region's cobbled square, towns for areas,
 * forest, rock and ponds where no region is; each quest's creature in its
 * region, and every party at its quest or walking there. Drag to look around;
 * everything on it is a button. It is night (Nightfall, art/relight.ts):
 * light falls at the hall's door and travels with every party out.
 */
export function WorldMap({ art, regions, quests, parties, characters, dungeons = [], onPickLead, onPickCharacter }: WorldMapProps) {
  const lookup = useIdentityLookup();
  const map = useMemo(() => buildTileMap(regions, art.tileSize), [regions, art.tileSize]);
  const layers = useMemo(() => TERRAINS.map((t) => ({ terrain: t, mask: map.masks[t] })), [map]);
  const [zoom, setZoom] = useState(1);
  const scroller = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; y: number; left: number; top: number } | null>(null);
  const width = map.cols * map.tile;
  const height = map.rows * map.tile;

  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    el.scrollLeft = map.hall.x * zoom - el.clientWidth / 2;
    el.scrollTop = map.hall.y * zoom - el.clientHeight / 2;
  }, [map, zoom]);

  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if ((e.target as HTMLElement).closest("button")) return;
    const el = scroller.current;
    if (el) drag.current = { x: e.clientX, y: e.clientY, left: el.scrollLeft, top: el.scrollTop };
  };
  const onPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const el = scroller.current;
    if (!drag.current || !el) return;
    el.scrollLeft = drag.current.left - (e.clientX - drag.current.x);
    el.scrollTop = drag.current.top - (e.clientY - drag.current.y);
  };
  const shownQuests = quests.filter((q) => map.regions.some((r) => r.repo === q.repo)).slice(0, 150);
  const out = parties.filter((p) => p.state !== "recalled");
  const fought = new Set(out.filter((p) => p.state === "working" && p.lead_id).map((p) => p.lead_id));
  const home = characters.filter((c) => c.state === "idle").slice(0, 10);

  return (
    <Card role="region" className="space-y-2 p-2" data-testid="world-map" aria-label="Map">
      <div className="flex items-center justify-between gap-2 px-1">
        <span className="text-xs text-fg-muted">
          {map.regions.length} repositories · drag to pan · click a quest to open it
        </span>
        <span className="flex gap-1">
          {[1, 2].map((z) => (
            <Button key={z} size="xs" variant={zoom === z ? "secondary" : "ghost"} aria-pressed={zoom === z} onClick={() => setZoom(z)}>
              {z}×
            </Button>
          ))}
        </span>
      </div>
      <div
        ref={scroller}
        className="nightfall night-frame relative h-[70vh] min-h-[320px] cursor-grab overflow-auto rounded-md active:cursor-grabbing"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={() => (drag.current = null)}
        onPointerLeave={() => (drag.current = null)}
      >
        <div style={{ width: width * zoom, height: height * zoom, position: "relative" }}>
          <div style={{ position: "absolute", left: 0, top: 0, width, height, transform: `scale(${zoom})`, transformOrigin: "0 0" }}>
            <TerrainCanvas art={art} cols={map.cols} rows={map.rows} base="grass" layers={layers} night />
            <Torchlight x={map.hall.x} y={map.hall.y - 16} radius={96} />
            {map.objects.map((o, i) => (
              <ArtObjectAt key={`${o.name}-${i}`} art={art} name={o.name} x={o.x} y={o.y} />
            ))}
            {map.regions.map((r) => (
              <span
                key={`label-${r.repo}`}
                className="sprite-nameplate pointer-events-none absolute -translate-x-1/2 whitespace-nowrap rounded-sm bg-black/55 px-1 text-primary"
                style={{ left: r.label.x, top: r.label.y - 14, zIndex: 50_000 }}
              >
                {placeOf(r.repo)}
              </span>
            ))}
            {map.regions.flatMap((r) =>
              r.towns.map((t) => (
                <span
                  key={`town-${r.repo}-${t.name}`}
                  className="sprite-nameplate pointer-events-none absolute -translate-x-1/2 whitespace-nowrap text-white"
                  style={{ left: t.x, top: t.y + 2, zIndex: 50_000, fontSize: 8, textShadow: "1px 1px 0 #000" }}
                >
                  {t.name}
                </span>
              )),
            )}
            {shownQuests.map((q) => {
              const spot = leadSpot(map, q.repo, q.id);
              const found = creatureFor(art, q.kind);
              const atlas = found ? art.atlases[found[1].atlas] : undefined;
              const label = { type: "button" as const, title: `${q.title} (level ${q.level})`, "aria-label": `${q.verb}: ${q.title}`, "data-testid": "map-lead" };
              if (!found || !atlas)
                return (
                  <button key={q.id} {...label} onClick={() => onPickLead(q)} style={{ ...anchored(spot.x, spot.y, { x: 12, y: 24 }), width: 24, height: 24 }}>
                    <CreatureIcon kind={q.kind} creature={q.creature} size={24} />
                  </button>
                );
              const [name, c] = found;
              return (
                <button key={q.id} {...label} onClick={() => onPickLead(q)} style={{ ...anchored(spot.x, spot.y, c.anchor), width: c.frames[0][2], height: c.frames[0][3] }}>
                  <ArtSprite image={atlas.image} rect={c.frames[0]} frames={c.frames} animation={`creature-${name}`} fps={c.fps} loop={c.loop} />
                  {fought.has(q.id) && (
                    <EffectAt art={art} name="hitsplat" x={c.anchor.x} y={c.anchor.y - 8} label={String(1 + (hash(q.id) % 9))} className="world-hitsplat" />
                  )}
                </button>
              );
            })}
            {dungeons.map((d) => {
              const spot = dungeonSpot(map, d);
              if (!spot) return null;
              // The worst open slop in it: what entering the dungeon opens.
              const worst = quests
                .filter((q) => q.kind === "slop" && q.repo === d.repo && (q.area ?? null) === d.area)
                .sort((a, b) => (b.slop_score ?? 0) - (a.slop_score ?? 0))[0];
              const where = d.area ?? placeOf(d.repo);
              // Its Slop-O-Meter reading (spec 2026-10-26): how much of it landed beyond human attention.
              const meter = d.meter ? ` · Slop-O-Meter ${d.meter.name} (${d.meter.score.toFixed(2)}${d.meter.provisional ? ", provisional" : ""})` : "";
              const title = `Slop Dungeon · ${where} · slop score ${d.score} · ${d.open} open, ${d.cleaned} cleaned lately${meter}`;
              return (
                <button
                  key={`dungeon-${d.repo}-${d.area ?? ""}`}
                  type="button"
                  title={title}
                  aria-label={title}
                  data-testid="map-dungeon"
                  data-score={d.score}
                  data-meter-level={d.meter?.level}
                  disabled={!worst}
                  onClick={() => worst && onPickLead(worst)}
                  style={{ ...anchored(spot.x, spot.y, { x: 12, y: 24 }), width: 24, height: 24 }}
                >
                  <CreatureIcon kind="dungeon" creature="slop_dungeon" size={24} label={title} />
                  <span
                    className="sprite-nameplate pointer-events-none absolute left-1/2 top-full -translate-x-1/2 whitespace-nowrap text-white"
                    style={{ fontSize: 8, textShadow: "1px 1px 0 #000" }}
                  >
                    Slop {d.score}
                    {d.meter && ` · ${d.meter.name}`}
                  </span>
                </button>
              );
            })}
            {out.map((p) => (
              <PartyOnMap key={p.job_id} map={map} art={art} party={p} onPick={() => onPickCharacter(p.leader)} />
            ))}
            {home.map((c, i) => (
              <button
                key={c.agent_type}
                type="button"
                title={lookup(c.agent_type).displayName}
                aria-label={lookup(c.agent_type).displayName}
                onClick={() => onPickCharacter(c.agent_type)}
                style={{ ...anchored(map.hall.x - 80 + (i % 5) * 34, map.hall.y + 44 + Math.floor(i / 5) * 30, { x: 16, y: 32 }), width: 32, height: 32 }}
              >
                <AgentAvatar identity={lookup(c.agent_type)} size={32} state="idle" />
              </button>
            ))}
          </div>
        </div>
      </div>
    </Card>
  );
}
