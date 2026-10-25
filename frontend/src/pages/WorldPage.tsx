import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import type { Lead, Party, World, WorldCharacter } from "@/api/world";
import { PageError } from "@/components/PageError";
import { Skeleton } from "@/components/ui/PageSkeleton";
import { CharacterPanel } from "@/components/world/CharacterPanel";
import { GuildHall } from "@/components/world/GuildHall";
import { QuestBoard } from "@/components/world/QuestBoard";
import { QuestInbox } from "@/components/world/QuestInbox";
import { QuestScrollDialog } from "@/components/world/QuestScroll";
import { SendPartyDialog } from "@/components/world/SendPartyDialog";
import { WorldChat } from "@/components/world/WorldChat";
import { WorldMap } from "@/components/world/WorldMap";
import { WriteQuestDialog } from "@/components/world/WriteQuestDialog";
import { useArt } from "@/components/world/art/manifest";
import "@/components/world/world.css";
import { useIdentityLookup } from "@/hooks/useAgentIdentity";
import { useWorld } from "@/hooks/useWorld";
import { levelUps, narrate, xpDrops, type NarrationLine } from "@/lib/world";
import { useAuthStore, useIsAdmin } from "@/stores/authStore";
import { cn } from "@/lib/utils";

/** How long an experience drop floats before it clears. */
const XP_DROP_MS = 2_600;

/**
 * The home screen (spec 2026-10-25): the player's guild. The characters in
 * the hall and where each is; the quests that came back, waiting for a
 * judgement; the quest board to send a party from; the map; the chat box.
 * The address keeps what is open (?character=, ?quest=, ?lead=, ?write=,
 * ?view=map), so a link can open a quest's scroll.
 */
export function WorldPage() {
  const world = useWorld();
  const art = useArt();
  const lookup = useIdentityLookup();
  const isAdmin = useIsAdmin();
  const by = useAuthStore((s) => s.user?.email) ?? "dashboard";
  const [params, setParams] = useSearchParams();
  const previous = useRef<World | null>(null);
  const [lines, setLines] = useState<NarrationLine[]>([]);
  const [drops, setDrops] = useState<Map<string, number>>(new Map());
  const [ups, setUps] = useState<ReadonlySet<string>>(new Set());

  useEffect(() => {
    if (!world.data) return;
    const before = previous.current;
    const said = narrate(before?.parties ?? null, world.data.parties, (agent) => lookup(agent).displayName);
    const rose = levelUps(before?.characters ?? null, world.data.characters);
    for (const [agent, level] of rose)
      said.push({ key: `${agent}:level:${level}`, text: `Congratulations, ${lookup(agent).displayName} reached level ${level}!`, tone: "win" });
    if (said.length) setLines((l) => [...l, ...said].slice(-30));
    const gained = xpDrops(before?.characters ?? null, world.data.characters);
    previous.current = world.data;
    if (gained.size || rose.size) {
      setDrops(gained);
      setUps(new Set(rose.keys()));
      const timer = window.setTimeout(() => {
        setDrops(new Map());
        setUps(new Set());
      }, XP_DROP_MS);
      return () => window.clearTimeout(timer);
    }
  }, [world.data, lookup]);

  const set = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    for (const k of ["character", "quest", "lead", "write"]) if (k !== key && key !== "view") next.delete(k);
    if (value === null) next.delete(key);
    else next.set(key, value);
    setParams(next, { replace: true });
  };

  const skillName = useMemo(() => {
    const names = new Map((world.data?.skills ?? []).map((s) => [s.id, s.name]));
    return (stat: string) => names.get(stat) ?? stat;
  }, [world.data?.skills]);

  if (world.isError) return <PageError message="The world did not load" onRetry={() => void world.refetch()} />;
  if (!world.data) return <Skeleton className="h-96 w-full" />;
  const data = world.data;
  const partyOf = new Map<string, Party>(data.parties.map((p) => [p.job_id, p]));
  const selectedCharacter: WorldCharacter | undefined = data.characters.find((c) => c.agent_type === params.get("character"));
  const selectedLead: Lead | undefined = data.quests.find((q) => q.id === params.get("lead"));
  const questId = params.get("quest");
  const view = params.get("view") === "map" ? "map" : "guild";
  const strongest = Math.max(1, ...data.characters.map((c) => c.level));
  const repos = [...new Set(data.regions.map((r) => r.repo))].sort();

  return (
    <div className="world hud space-y-3" data-testid="world-page">
      <header className="world-panel flex flex-wrap items-center justify-between gap-2 p-3">
        <div>
          <h1 className="world-title title-pixel text-xl">Your guild</h1>
          <p className="world-muted text-xs" data-testid="world-summary">
            {data.characters.length} characters · {data.characters.filter((c) => c.state !== "idle").length} out ·{" "}
            {data.quests.length} quests on the board · account total {data.account.total_level.toLocaleString("en-US")}
          </p>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <button type="button" className={cn("world-button", view === "guild" && "ring-1 ring-[var(--gold)]")} onClick={() => set("view", null)}>
            Guild
          </button>
          <button type="button" className={cn("world-button", view === "map" && "ring-1 ring-[var(--gold)]")} onClick={() => set("view", "map")}>
            Map
          </button>
          <button type="button" className="world-button" data-tone="win" disabled={!isAdmin} onClick={() => set("write", "1")}>
            Write a quest
          </button>
        </div>
      </header>

      <QuestInbox quests={data.inbox} onOpen={(jobId) => set("quest", jobId)} />

      {view === "map" ? (
        <WorldMap
          art={art}
          regions={data.regions}
          quests={data.quests}
          parties={data.parties}
          characters={data.characters}
          onPickLead={(lead) => set("lead", lead.id)}
          onPickCharacter={(agent) => set("character", agent)}
        />
      ) : (
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1fr)_380px]">
          <GuildHall
            art={art}
            characters={data.characters}
            parties={data.parties}
            loot={data.loot}
            skillName={skillName}
            xpDrops={drops}
            levelUps={ups}
            selected={selectedCharacter?.agent_type ?? null}
            onSelect={(agent) => set("character", agent)}
            onBoard={() => document.getElementById("quest-board-title")?.scrollIntoView({ behavior: "smooth", block: "start" })}
            onLoot={(jobId) => set("quest", jobId)}
          />
          <div className="lg:sticky lg:top-3 lg:max-h-[calc(100vh-6rem)]">
            <QuestBoard quests={data.quests} partyLevel={strongest} onPick={(lead) => set("lead", lead.id)} />
          </div>
        </div>
      )}

      <div className="sticky bottom-2 z-10 max-w-md">
        <WorldChat lines={lines} />
      </div>

      {selectedCharacter && (
        <CharacterPanel
          character={selectedCharacter}
          party={selectedCharacter.job_id ? partyOf.get(selectedCharacter.job_id) : undefined}
          skillName={skillName}
          canAct={isAdmin}
          onClose={() => set("character", null)}
          onOpenQuest={(jobId) => set("quest", jobId)}
          onWriteQuest={(agent) => {
            const next = new URLSearchParams(params);
            next.delete("character");
            next.set("write", agent);
            setParams(next, { replace: true });
          }}
        />
      )}
      {selectedLead && (
        <SendPartyDialog
          lead={selectedLead}
          characters={data.characters}
          by={by}
          canSend={isAdmin}
          onClose={() => set("lead", null)}
          onSent={() => set("lead", null)}
        />
      )}
      {questId && <QuestScrollDialog jobId={questId} skillName={skillName} canAct={isAdmin} onClose={() => set("quest", null)} />}
      {params.get("write") && (
        <WriteQuestDialog
          characters={data.characters}
          repos={repos}
          initialAgent={params.get("write") === "1" ? null : params.get("write")}
          onClose={() => set("write", null)}
          onSent={() => set("write", null)}
        />
      )}
    </div>
  );
}
