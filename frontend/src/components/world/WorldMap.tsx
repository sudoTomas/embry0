import { useMemo } from "react";
import type { Lead, Party, Region, WorldCharacter } from "@/api/world";
import { AgentAvatar } from "@/components/agents/AgentAvatar";
import { useIdentityLookup } from "@/hooks/useAgentIdentity";
import { STATE_POSE, placeOf } from "@/lib/world";
import { layoutRegions, mapBounds, spotIn } from "@/lib/worldMap";
import { CreatureIcon } from "./CreatureIcon";

interface WorldMapProps {
  regions: readonly Region[];
  quests: readonly Lead[];
  parties: readonly Party[];
  characters: readonly WorldCharacter[];
  onPickLead: (lead: Lead) => void;
  onPickCharacter: (agentType: string) => void;
}

/**
 * The overworld, phase 1: regions around the guild hall, towns for areas,
 * the board's creatures where they stand, and every party at its quest (or
 * walking there: a party's place moves, and the move is animated). Drawn as
 * SVG and HTML over it; the art manifest's tiles replace the flat ground
 * when they land.
 */
export function WorldMap({ regions, quests, parties, characters, onPickLead, onPickCharacter }: WorldMapProps) {
  const lookup = useIdentityLookup();
  const placed = useMemo(() => layoutRegions(regions), [regions]);
  const bounds = useMemo(() => mapBounds(placed), [placed]);
  const boxOf = useMemo(() => new Map(placed.map((p) => [p.region.repo, p.box])), [placed]);
  const pct = (x: number, y: number) => ({ left: `${((x - bounds.x) / bounds.w) * 100}%`, top: `${((y - bounds.y) / bounds.h) * 100}%` });
  const hall = { x: 0, y: 0 };

  const leadSpot = (lead: { id: string; repo: string }) => {
    const box = boxOf.get(lead.repo);
    return box ? spotIn(box, lead.id) : hall;
  };
  const partySpot = (party: Party) => {
    if (party.state === "returning" || party.state === "recalled") return hall;
    const box = party.repo ? boxOf.get(party.repo) : undefined;
    if (!box) return hall;
    return party.lead_id ? spotIn(box, party.lead_id) : spotIn(box, party.job_id, 0.35);
  };
  const shown = quests.filter((q) => boxOf.has(q.repo)).slice(0, 120);
  const out = parties.filter((p) => p.state !== "returning" && p.state !== "recalled" && p.state !== "defeated");
  const home = characters.filter((c) => c.state === "idle").slice(0, 12);

  return (
    <section className="world-panel overflow-hidden p-2" data-testid="world-map" aria-label="The world map">
      <div className="relative w-full" style={{ aspectRatio: `${bounds.w} / ${bounds.h}`, minHeight: 320, background: "#3f6b2f" }}>
        <svg className="absolute inset-0 h-full w-full" viewBox={`${bounds.x} ${bounds.y} ${bounds.w} ${bounds.h}`} preserveAspectRatio="none" aria-hidden="true">
          <defs>
            <pattern id="grass" width="16" height="16" patternUnits="userSpaceOnUse">
              <rect width="16" height="16" fill="#46782f" />
              <rect x="3" y="5" width="1" height="2" fill="#5a9440" />
              <rect x="11" y="10" width="1" height="2" fill="#5a9440" />
            </pattern>
          </defs>
          <rect x={bounds.x} y={bounds.y} width={bounds.w} height={bounds.h} fill="url(#grass)" />
          {placed.map((p) => (
            <line
              key={`road-${p.region.repo}`}
              x1={0}
              y1={0}
              x2={p.box.x + p.box.w / 2}
              y2={p.box.y + p.box.h / 2}
              stroke="#a08355"
              strokeWidth={6}
              strokeDasharray="10 4"
              opacity={0.75}
            />
          ))}
          {placed.map((p) => (
            <g key={p.region.repo}>
              <rect x={p.box.x} y={p.box.y} width={p.box.w} height={p.box.h} fill={p.region.watched ? "#557a3a" : "#4d6f35"} stroke="#2b3b1d" strokeWidth={3} rx={6} />
              {p.towns.map((t) => (
                <g key={t.name}>
                  <rect x={t.x - 9} y={t.y - 7} width={18} height={14} fill="#8b6b45" stroke="#3a2a14" strokeWidth={2} />
                  <polygon points={`${t.x - 11},${t.y - 7} ${t.x},${t.y - 16} ${t.x + 11},${t.y - 7}`} fill="#a33d2b" stroke="#3a2a14" strokeWidth={2} />
                </g>
              ))}
            </g>
          ))}
          <rect x={-34} y={-26} width={68} height={52} fill="#7a6248" stroke="#2b1d0e" strokeWidth={3} />
          <polygon points="-42,-26 0,-56 42,-26" fill="#6b2b20" stroke="#2b1d0e" strokeWidth={3} />
          <rect x={-8} y={4} width={16} height={22} fill="#3a2614" />
        </svg>

        {placed.map((p) => (
          <span key={`name-${p.region.repo}`} className="world-title pointer-events-none absolute -translate-x-1/2 text-[11px]" style={pct(p.box.x + p.box.w / 2, p.box.y + 4)}>
            {placeOf(p.region.repo)}
          </span>
        ))}
        {placed.flatMap((p) =>
          p.towns.map((t) => (
            <span key={`town-${p.region.repo}-${t.name}`} className="world-text pointer-events-none absolute -translate-x-1/2 text-[9px]" style={pct(t.x, t.y + 10)}>
              {t.name}
            </span>
          )),
        )}
        <span className="world-title pointer-events-none absolute -translate-x-1/2 text-xs" style={pct(0, 30)}>
          Guild hall
        </span>

        {shown.map((q) => {
          const spot = leadSpot(q);
          return (
            <button
              key={q.id}
              type="button"
              className="absolute -translate-x-1/2 -translate-y-1/2"
              style={pct(spot.x, spot.y)}
              title={`${q.title} (level ${q.level})`}
              onClick={() => onPickLead(q)}
              data-testid="map-lead"
            >
              <CreatureIcon creature={q.creature} size={24} label={q.title} />
            </button>
          );
        })}

        {out.map((p) => {
          const spot = partySpot(p);
          const identity = lookup(p.leader);
          return (
            <button
              key={p.job_id}
              type="button"
              className="absolute -translate-x-1/2 -translate-y-full transition-[left,top] duration-[2500ms] ease-linear"
              style={pct(spot.x + 12, spot.y)}
              title={`${identity.displayName}: ${p.title}`}
              onClick={() => onPickCharacter(p.leader)}
              data-testid="map-party"
              data-state={p.state}
            >
              {p.state === "waiting" && <span className="world-bubble absolute -top-4 left-1/2 -translate-x-1/2">?</span>}
              <AgentAvatar identity={identity} size={32} state={STATE_POSE[p.state]} />
            </button>
          );
        })}

        <div className="absolute flex -translate-x-1/2 gap-0.5" style={pct(0, -4)}>
          {home.map((c) => (
            <button key={c.agent_type} type="button" title={lookup(c.agent_type).displayName} onClick={() => onPickCharacter(c.agent_type)}>
              <AgentAvatar identity={lookup(c.agent_type)} size={16} state="idle" />
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}
